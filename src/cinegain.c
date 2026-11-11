/*
 * cinegain.c - stereo "film voice" processor as a self-contained LADSPA plugin.
 *
 * Signal flow (all stages stereo-linked, so the stereo image never shifts):
 *   input gain -> high-pass -> bass shelf -> dialog peak -> treble shelf
 *   -> mid/side balance -> leveler (slow RMS boost, fast peak-sensing cut)
 *   -> compressor -> makeup -> look-ahead peak limiter -> soft safety clip
 *
 * The four meter values are also written to a small memory-mapped file
 * ($CINEGAIN_METER_DIR or $XDG_RUNTIME_DIR/cinegain/meter-<pid>.bin) because
 * PipeWire does not expose control output ports; the GUI reads that file.
 *
 * Loaded by PipeWire's filter-chain (ladspa plugin type). Every control port
 * becomes a live-adjustable PipeWire Prop "<node>:<port name>".
 *
 * The minimal LADSPA ABI is declared inline (LADSPA 1.1, LGPL interface), so
 * no ladspa.h package is required to build.
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

/* ---- minimal LADSPA 1.1 ABI ------------------------------------------- */
typedef float LADSPA_Data;
typedef void *LADSPA_Handle;
typedef int LADSPA_Properties;
typedef int LADSPA_PortDescriptor;
typedef int LADSPA_PortRangeHintDescriptor;

#define LADSPA_PROPERTY_HARD_RT_CAPABLE 0x4
#define LADSPA_PORT_INPUT 0x1
#define LADSPA_PORT_OUTPUT 0x2
#define LADSPA_PORT_CONTROL 0x4
#define LADSPA_PORT_AUDIO 0x8
#define LADSPA_HINT_BOUNDED_BELOW 0x1
#define LADSPA_HINT_BOUNDED_ABOVE 0x2
#define LADSPA_HINT_TOGGLED 0x4
#define LADSPA_HINT_DEFAULT_MIDDLE 0x80 /* unused, we use explicit defaults */
#define LADSPA_HINT_DEFAULT_0 0x200
#define LADSPA_HINT_DEFAULT_1 0x240

typedef struct {
	LADSPA_PortRangeHintDescriptor HintDescriptor;
	LADSPA_Data LowerBound;
	LADSPA_Data UpperBound;
} LADSPA_PortRangeHint;

typedef struct _LADSPA_Descriptor {
	unsigned long UniqueID;
	const char *Label;
	LADSPA_Properties Properties;
	const char *Name;
	const char *Maker;
	const char *Copyright;
	unsigned long PortCount;
	const LADSPA_PortDescriptor *PortDescriptors;
	const char *const *PortNames;
	const LADSPA_PortRangeHint *PortRangeHints;
	void *ImplementationData;
	LADSPA_Handle (*instantiate)(const struct _LADSPA_Descriptor *, unsigned long);
	void (*connect_port)(LADSPA_Handle, unsigned long, LADSPA_Data *);
	void (*activate)(LADSPA_Handle);
	void (*run)(LADSPA_Handle, unsigned long);
	void (*run_adding)(LADSPA_Handle, unsigned long);
	void (*set_run_adding_gain)(LADSPA_Handle, LADSPA_Data);
	void (*deactivate)(LADSPA_Handle);
	void (*cleanup)(LADSPA_Handle);
} LADSPA_Descriptor;

/* ---- ports ------------------------------------------------------------- */
enum {
	P_IN_L, P_IN_R, P_OUT_L, P_OUT_R,
	P_BYPASS,
	P_INPUT_DB,
	P_HIGHPASS_HZ,
	P_BASS_DB,
	P_DIALOG_DB, P_DIALOG_HZ, P_DIALOG_Q,
	P_TREBLE_DB,
	P_MID_DB, P_SIDE_DB,
	P_LEVELER, P_LEV_TARGET_DB, P_LEV_MAX_BOOST_DB, P_LEV_MAX_CUT_DB,
	P_LEV_SPEED_S, P_LEV_GATE_DB,
	P_COMP_THRESH_DB, P_COMP_RATIO, P_COMP_KNEE_DB,
	P_COMP_ATTACK_MS, P_COMP_RELEASE_MS,
	P_MAKEUP_DB,
	P_LIM_CEILING_DB, P_LIM_RELEASE_MS,
	P_METER_LEVEL_DB, P_METER_LEVELER_DB, P_METER_GR_DB, P_METER_LIMIT_DB,
	P_COUNT
};

#define CIN (LADSPA_PORT_INPUT | LADSPA_PORT_CONTROL)
#define COUT (LADSPA_PORT_OUTPUT | LADSPA_PORT_CONTROL)
#define AIN (LADSPA_PORT_INPUT | LADSPA_PORT_AUDIO)
#define AOUT (LADSPA_PORT_OUTPUT | LADSPA_PORT_AUDIO)
#define BB (LADSPA_HINT_BOUNDED_BELOW | LADSPA_HINT_BOUNDED_ABOVE)

static const LADSPA_PortDescriptor port_desc[P_COUNT] = {
	AIN, AIN, AOUT, AOUT,
	CIN, CIN, CIN, CIN, CIN, CIN, CIN, CIN, CIN, CIN,
	CIN, CIN, CIN, CIN, CIN, CIN,
	CIN, CIN, CIN, CIN, CIN, CIN, CIN, CIN,
	COUT, COUT, COUT, COUT,
};

static const char *const port_names[P_COUNT] = {
	"In L", "In R", "Out L", "Out R",
	"bypass",
	"input_db",
	"highpass_hz",
	"bass_db",
	"dialog_db", "dialog_hz", "dialog_q",
	"treble_db",
	"mid_db", "side_db",
	"leveler", "lev_target_db", "lev_max_boost_db", "lev_max_cut_db",
	"lev_speed_s", "lev_gate_db",
	"comp_thresh_db", "comp_ratio", "comp_knee_db",
	"comp_attack_ms", "comp_release_ms",
	"makeup_db",
	"lim_ceiling_db", "lim_release_ms",
	"meter_level_db", "meter_leveler_db", "meter_gr_db", "meter_limit_db",
};

/* Defaults are documented here and applied by the cinegain CLI; LADSPA's
 * coarse default hints cannot express arbitrary values. */
static const LADSPA_PortRangeHint port_hints[P_COUNT] = {
	{0, 0, 0}, {0, 0, 0}, {0, 0, 0}, {0, 0, 0},
	{LADSPA_HINT_TOGGLED | LADSPA_HINT_DEFAULT_0, 0, 1}, /* bypass */
	{BB, -30, 40},     /* input_db (normal scale -30..+15, overdrive up to +40) */
	{BB, 10, 400},     /* highpass_hz */
	{BB, -15, 15},     /* bass_db */
	{BB, -15, 15},     /* dialog_db */
	{BB, 300, 8000},   /* dialog_hz */
	{BB, 0.2f, 5},     /* dialog_q */
	{BB, -15, 15},     /* treble_db */
	{BB, -15, 15},     /* mid_db */
	{BB, -30, 15},     /* side_db */
	{LADSPA_HINT_TOGGLED | LADSPA_HINT_DEFAULT_1, 0, 1}, /* leveler */
	{BB, -45, -6},     /* lev_target_db */
	{BB, 0, 36},       /* lev_max_boost_db */
	{BB, 0, 36},       /* lev_max_cut_db */
	{BB, 0.2f, 60},    /* lev_speed_s */
	{BB, -100, -20},   /* lev_gate_db */
	{BB, -70, 0},      /* comp_thresh_db */
	{BB, 1, 30},       /* comp_ratio */
	{BB, 0, 30},       /* comp_knee_db */
	{BB, 0.1f, 500},   /* comp_attack_ms */
	{BB, 5, 5000},     /* comp_release_ms */
	{BB, -24, 40},     /* makeup_db */
	{BB, -24, 0},      /* lim_ceiling_db */
	{BB, 5, 2000},     /* lim_release_ms */
	{BB, -120, 20}, {BB, -60, 60}, {BB, -60, 0}, {BB, -60, 0}, /* meters */
};

/* ---- DSP helpers --------------------------------------------------------- */
#define LOOKAHEAD_MS 5.0
#define MAX_LOOKAHEAD 2048
#define DENORM 1e-20f
#define DQ_MASK (MAX_LOOKAHEAD - 1) /* MAX_LOOKAHEAD is a power of two */
/* A switched-off leveler this close to 0 dB is snapped to 0 dB and skipped (#41);
 * 1e-4 dB is a gain error of about 1e-5, far below anything audible. */
#define LEV_IDLE_DB 1e-4

/* Building with -DCINEGAIN_REFERENCE keeps the plain O(n) limiter scan and runs
 * every stage on every sample. Only the tests use it, as the oracle for the
 * optimized build (#41). */

typedef struct { double b0, b1, b2, a1, a2; } biquad_coef;
typedef struct { double z1, z2; } biquad_state;

static inline double db2lin(double db) { return pow(10.0, db / 20.0); }
static inline double lin2db(double v) { return 20.0 * log10(v > 1e-12 ? v : 1e-12); }
static inline double coef_ms(double ms, double rate)
{
	if (ms <= 0.0) return 0.0;
	return exp(-1.0 / (ms * 0.001 * rate));
}

/* RBJ audio EQ cookbook biquads. */
static void bq_set(biquad_coef *c, int type, double rate, double f0, double q, double gain_db)
{
	double A = pow(10.0, gain_db / 40.0);
	if (f0 > rate * 0.45) f0 = rate * 0.45;
	double w0 = 2.0 * M_PI * f0 / rate;
	double cw = cos(w0), sw = sin(w0);
	double alpha = sw / (2.0 * q);
	double b0, b1, b2, a0, a1, a2;
	switch (type) {
	case 0: /* high-pass */
		b0 = (1 + cw) / 2; b1 = -(1 + cw); b2 = (1 + cw) / 2;
		a0 = 1 + alpha; a1 = -2 * cw; a2 = 1 - alpha;
		break;
	case 1: { /* low shelf (S = 1) */
		double sa = 2 * sqrt(A) * alpha;
		b0 = A * ((A + 1) - (A - 1) * cw + sa);
		b1 = 2 * A * ((A - 1) - (A + 1) * cw);
		b2 = A * ((A + 1) - (A - 1) * cw - sa);
		a0 = (A + 1) + (A - 1) * cw + sa;
		a1 = -2 * ((A - 1) + (A + 1) * cw);
		a2 = (A + 1) + (A - 1) * cw - sa;
		break; }
	case 2: /* peaking */
		b0 = 1 + alpha * A; b1 = -2 * cw; b2 = 1 - alpha * A;
		a0 = 1 + alpha / A; a1 = -2 * cw; a2 = 1 - alpha / A;
		break;
	default: { /* high shelf (S = 1) */
		double sa = 2 * sqrt(A) * alpha;
		b0 = A * ((A + 1) + (A - 1) * cw + sa);
		b1 = -2 * A * ((A - 1) + (A + 1) * cw);
		b2 = A * ((A + 1) + (A - 1) * cw - sa);
		a0 = (A + 1) - (A - 1) * cw + sa;
		a1 = 2 * ((A - 1) - (A + 1) * cw);
		a2 = (A + 1) - (A - 1) * cw - sa;
		break; }
	}
	c->b0 = b0 / a0; c->b1 = b1 / a0; c->b2 = b2 / a0;
	c->a1 = a1 / a0; c->a2 = a2 / a0;
}

static inline double bq_run(const biquad_coef *c, biquad_state *s, double x)
{
	/* transposed direct form II */
	double y = c->b0 * x + s->z1;
	s->z1 = c->b1 * x - c->a1 * y + s->z2;
	s->z2 = c->b2 * x - c->a2 * y;
	return y;
}

enum { EQ_HP, EQ_BASS, EQ_DIALOG, EQ_TREBLE, EQ_N };

#define METER_MAGIC 0x434E4D31u /* "CNM1" */
typedef struct {
	uint32_t magic;
	uint32_t seq;
	float level_db, leveler_db, gr_db, limit_db;
	float ramp_db; /* dB still to go before every gain has reached its target (0 = settled) */
} meter_shm;

/* Gains only rise this fast; a lower value applies at once (user decision 2026-11-11):
 * 0 -> +25 dB takes 2.5 s, so there is time to react, while turning down is instant. */
#define RAMP_DB_PER_S 10.0
enum { RG_INPUT, RG_BASS, RG_DIALOG, RG_TREBLE, RG_MID, RG_SIDE, RG_MAKEUP, RG_N };
static const int ramp_port[RG_N] = {
	P_INPUT_DB, P_BASS_DB, P_DIALOG_DB, P_TREBLE_DB, P_MID_DB, P_SIDE_DB, P_MAKEUP_DB,
};

typedef struct {
	LADSPA_Data *port[P_COUNT];
	double rate;
	meter_shm *shm;
	char shm_path[512];
	dev_t shm_dev;       /* identity of our meter file, so cleanup only removes our own */
	ino_t shm_ino;
	double lev_peak;     /* fast peak envelope for the leveler's cut path */

	/* cached parameter values to detect changes */
	float eq_cache[6];
	int eq_valid;
	biquad_coef eq[EQ_N];
	biquad_state eqs[EQ_N][2];

	/* leveler */
	double lev_power;    /* mean-square power estimate */
	double lev_gain_db;  /* current applied leveler gain */

	/* compressor */
	double comp_env_db;  /* detector level in dB */

	/* limiter */
	float delay[2][MAX_LOOKAHEAD];
#ifdef CINEGAIN_REFERENCE
	float need[MAX_LOOKAHEAD]; /* required gain per sample, ring buffer */
#else
	/* sliding minimum of the required gain over the look-ahead window: a monotone
	 * deque (ring buffer) of (value, sample index), values rising from the front */
	float dq_val[MAX_LOOKAHEAD];
	uint32_t dq_idx[MAX_LOOKAHEAD];
	unsigned dq_head, dq_len;
	uint32_t lim_t;      /* running sample index (wraps; only differences are used) */
#endif
	unsigned la_len, la_pos;
	double lim_gain;

	/* meters (block peak/mean values) */
	double meter_level, meter_gr, meter_limit;

	/* applied (ramped) values of the gain controls */
	double ramp[RG_N];
	int ramp_valid;

	/* bypass -> active: the dry signal hands over to the processed one */
	int was_bypassed;
	int xf_on;
	unsigned xf_pos;     /* samples since processing resumed */
} Cinegain;

static LADSPA_Handle instantiate(const LADSPA_Descriptor *d, unsigned long rate)
{
	(void)d;
	Cinegain *k = calloc(1, sizeof(*k));
	if (!k) return NULL;
	k->rate = (double)rate;
	unsigned n = (unsigned)(LOOKAHEAD_MS * 0.001 * rate);
	if (n < 1) n = 1;
	if (n > MAX_LOOKAHEAD) n = MAX_LOOKAHEAD;
	k->la_len = n;

	/* Best-effort meter file; the plugin works fine without it. */
	const char *dir = getenv("CINEGAIN_METER_DIR");
	char buf[400];
	if (!dir || !*dir) {
		const char *xdg = getenv("XDG_RUNTIME_DIR");
		if (xdg && *xdg) { snprintf(buf, sizeof buf, "%s/cinegain", xdg); dir = buf; }
	}
	if (dir && *dir) {
		/* Every instance gets a fresh inode under the stable name meter-<pid>.bin:
		 * build it under a unique temp name, then rename it into place. The GUI keeps
		 * reading meter-<pid>.bin, and an old instance cleaned up after a graph reset
		 * in the same process sees a different inode and leaves the new file alone (#35). */
		static unsigned meter_serial;
		unsigned serial = __atomic_fetch_add(&meter_serial, 1, __ATOMIC_RELAXED);
		char tmp[sizeof k->shm_path + 32];
		struct stat st;
		mkdir(dir, 0700);
		snprintf(k->shm_path, sizeof k->shm_path, "%s/meter-%d.bin", dir, (int)getpid());
		snprintf(tmp, sizeof tmp, "%s.%u.tmp", k->shm_path, serial);
		unlink(tmp); /* leftover of a crashed process that had the same pid */
		int fd = open(tmp, O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
		if (fd >= 0) {
			if (ftruncate(fd, sizeof(meter_shm)) == 0 && fstat(fd, &st) == 0) {
				void *m = mmap(NULL, sizeof(meter_shm), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
				if (m != MAP_FAILED) {
					k->shm = m;
					k->shm->magic = METER_MAGIC;
					k->shm_dev = st.st_dev;
					k->shm_ino = st.st_ino;
				}
			}
			close(fd);
			if (k->shm && rename(tmp, k->shm_path) != 0) {
				munmap(k->shm, sizeof(meter_shm));
				k->shm = NULL;
			}
			if (!k->shm) unlink(tmp);
		}
		if (!k->shm) k->shm_path[0] = 0;
	}
	return k;
}

static void connect_port(LADSPA_Handle h, unsigned long p, LADSPA_Data *data)
{
	Cinegain *k = h;
	if (p < P_COUNT) k->port[p] = data;
}

static void lim_window_reset(Cinegain *k)
{
#ifdef CINEGAIN_REFERENCE
	for (unsigned i = 0; i < MAX_LOOKAHEAD; i++) k->need[i] = 1.0f;
#else
	k->dq_head = k->dq_len = 0;
	k->lim_t = 0;
#endif
}

static void activate(LADSPA_Handle h)
{
	Cinegain *k = h;
	memset(k->eqs, 0, sizeof(k->eqs));
	memset(k->delay, 0, sizeof(k->delay));
	lim_window_reset(k);
	k->la_pos = 0;
	k->lim_gain = 1.0;
	k->lev_power = 0.0;
	k->lev_gain_db = 0.0;
	k->lev_peak = 0.0;
	k->comp_env_db = -120.0;
	k->eq_valid = 0;
	k->ramp_valid = 0;
	k->was_bypassed = 0;
	k->xf_on = 0;
}

/* Bypass -> active: no stale audio from the look-ahead line and no leveler boost
 * at once (#28). The leveler restarts at 0 dB at most and glides up as usual; the
 * compressor detector and the leveler's power/peak estimates stay, because they
 * can only hold the level down. The first LOOKAHEAD_MS keep the dry signal while
 * the line fills, the next LOOKAHEAD_MS crossfade to the processed signal. */
static void resume_after_bypass(Cinegain *k)
{
	memset(k->eqs, 0, sizeof(k->eqs));
	memset(k->delay, 0, sizeof(k->delay));
	lim_window_reset(k);
	k->la_pos = 0;
	k->lim_gain = 1.0;
	if (k->lev_gain_db > 0.0) k->lev_gain_db = 0.0;
	k->was_bypassed = 0;
	k->xf_on = 1;
	k->xf_pos = 0;
}


/* Control value clamped to the port's LADSPA range; NaN/Inf or an unconnected
 * port give the fallback (the documented default). */
static inline float ctl(Cinegain *k, int p, float fallback)
{
	if (!k->port[p]) return fallback;
	float v = *k->port[p];
	if (!isfinite(v)) return fallback;
	const LADSPA_PortRangeHint *h = &port_hints[p];
	if ((h->HintDescriptor & LADSPA_HINT_BOUNDED_BELOW) && v < h->LowerBound) return h->LowerBound;
	if ((h->HintDescriptor & LADSPA_HINT_BOUNDED_ABOVE) && v > h->UpperBound) return h->UpperBound;
	return v;
}

/* Moves every applied gain towards its control value: down at once, up at
 * RAMP_DB_PER_S. A fresh instance starts at 0 dB (or lower), so a sink that starts
 * with a big boost fades in instead of blasting. Returns the dB still to go. */
static double update_ramps(Cinegain *k, unsigned long n)
{
	const double step = RAMP_DB_PER_S * (double)n / k->rate;
	double left = 0.0;
	for (int g = 0; g < RG_N; g++) {
		double want = ctl(k, ramp_port[g], 0);
		if (!k->ramp_valid) k->ramp[g] = want < 0.0 ? want : 0.0;
		if (want <= k->ramp[g]) k->ramp[g] = want;
		else if (want - k->ramp[g] <= step) k->ramp[g] = want;
		else k->ramp[g] += step;
		if (want - k->ramp[g] > left) left = want - k->ramp[g];
	}
	k->ramp_valid = 1;
	return left;
}

/* Static soft-knee curve: gain reduction in dB (<= 0) for detector level e. */
static inline double comp_curve(double e, double thr, double ratio, double knee)
{
	double over = e - thr, gr = 0.0;
	if (knee > 0.0 && 2.0 * fabs(over) <= knee) {
		double t = over + knee / 2.0;
		gr = (1.0 / ratio - 1.0) * t * t / (2.0 * knee);
	} else if (over > 0.0) {
		gr = (1.0 / ratio - 1.0) * over;
	}
	return gr;
}

static void update_eq(Cinegain *k)
{
	float v[6] = {
		ctl(k, P_HIGHPASS_HZ, 20), (float)k->ramp[RG_BASS], (float)k->ramp[RG_DIALOG],
		ctl(k, P_DIALOG_HZ, 2500), ctl(k, P_DIALOG_Q, 0.9f), (float)k->ramp[RG_TREBLE],
	};
	if (k->eq_valid && memcmp(v, k->eq_cache, sizeof(v)) == 0) return;
	memcpy(k->eq_cache, v, sizeof(v));
	k->eq_valid = 1;
	/* The port's lower bound (10 Hz) means "off": a flat, transparent stage. */
	if (v[0] <= 10.5f) {
		k->eq[EQ_HP].b0 = 1.0;
		k->eq[EQ_HP].b1 = k->eq[EQ_HP].b2 = 0.0;
		k->eq[EQ_HP].a1 = k->eq[EQ_HP].a2 = 0.0;
	} else {
		bq_set(&k->eq[EQ_HP], 0, k->rate, v[0], 0.7071, 0);
	}
	bq_set(&k->eq[EQ_BASS], 1, k->rate, 120.0, 0.7071, v[1]);
	bq_set(&k->eq[EQ_DIALOG], 2, k->rate, v[3], v[4] > 0.05f ? v[4] : 0.05f, v[2]);
	bq_set(&k->eq[EQ_TREBLE], 3, k->rate, 8000.0, 0.7071, v[5]);
}

static void run(LADSPA_Handle h, unsigned long n)
{
	Cinegain *k = h;
	const float *inL = k->port[P_IN_L], *inR = k->port[P_IN_R];
	float *outL = k->port[P_OUT_L], *outR = k->port[P_OUT_R];
	if (!inL || !inR || !outL || !outR) return;

	if (ctl(k, P_BYPASS, 0) > 0.5f) {
		k->ramp_valid = 0; /* switching the filter back on fades the boost in again */
		k->was_bypassed = 1;
		if (outL != inL) memmove(outL, inL, n * sizeof(float));
		if (outR != inR) memmove(outR, inR, n * sizeof(float));
		return;
	}

	if (k->was_bypassed) resume_after_bypass(k);

	/* gains glide linearly across the block from their old to their new value */
	const double in_from = k->ramp_valid ? db2lin(k->ramp[RG_INPUT]) : -1.0;
	const double mk_from = k->ramp_valid ? db2lin(k->ramp[RG_MAKEUP]) : -1.0;
	const double ramp_left = update_ramps(k, n);
	update_eq(k);

	const double rate = k->rate;
	const double in_gain = db2lin(k->ramp[RG_INPUT]);
	const double in_start = in_from < 0.0 ? in_gain : in_from;
	const double in_inc = n ? (in_gain - in_start) / (double)n : 0.0;
	const double mid_g = db2lin(k->ramp[RG_MID]);
	const double side_g = db2lin(k->ramp[RG_SIDE]);

	const int lev_on = ctl(k, P_LEVELER, 1) > 0.5f;
#ifdef CINEGAIN_REFERENCE
	const int lev_idle = 0;
#else
	/* switched off and back at 0 dB: the stage is the identity, skip it (#41) */
	if (!lev_on && fabs(k->lev_gain_db) < LEV_IDLE_DB) k->lev_gain_db = 0.0;
	const int lev_idle = !lev_on && k->lev_gain_db == 0.0;
#endif
	const double lev_target = ctl(k, P_LEV_TARGET_DB, -20);
	const double lev_boost = ctl(k, P_LEV_MAX_BOOST_DB, 12);
	const double lev_cut = ctl(k, P_LEV_MAX_CUT_DB, 6);
	const double lev_speed = ctl(k, P_LEV_SPEED_S, 4);
	const double lev_gate = ctl(k, P_LEV_GATE_DB, -55);
	/* power averaging ~ speed/4, gain glide ~ speed */
	const double lev_pc = coef_ms(lev_speed * 250.0, rate);
	const double lev_gc = coef_ms(lev_speed * 1000.0, rate);
	/* Cuts follow loud material about 8x faster than boosts follow quiet material. */
	const double lev_gc_cut = coef_ms(lev_speed * 125.0, rate);
	const double peak_rel = coef_ms(150.0, rate);
	const double peak_room_db = 9.0; /* allowed peak-over-target before the fast cut engages */

	const double thr = ctl(k, P_COMP_THRESH_DB, -24);
	const double ratio = ctl(k, P_COMP_RATIO, 3) < 1.0f ? 1.0 : ctl(k, P_COMP_RATIO, 3);
	const double knee = ctl(k, P_COMP_KNEE_DB, 6);
	const double att = coef_ms(ctl(k, P_COMP_ATTACK_MS, 10), rate);
	const double rel = coef_ms(ctl(k, P_COMP_RELEASE_MS, 250), rate);
#ifdef CINEGAIN_REFERENCE
	const int comp_idle = 0;
#else
	/* ratio 1:1 gives exactly 0 dB gain reduction at every level: skip the curve and
	 * its db2lin. The detector keeps running, so leaving 1:1 behaves exactly as before. */
	const int comp_idle = ratio == 1.0;
#endif
	const double makeup = db2lin(k->ramp[RG_MAKEUP]);
	const double mk_start = mk_from < 0.0 ? makeup : mk_from;
	const double mk_inc = n ? (makeup - mk_start) / (double)n : 0.0;

	const double ceil_lin = db2lin(ctl(k, P_LIM_CEILING_DB, -1));
	const double lim_rel = coef_ms(ctl(k, P_LIM_RELEASE_MS, 100), rate);
	const double lim_att = coef_ms(LOOKAHEAD_MS / 3.0, rate);
	const unsigned la = k->la_len;

	double blk_peak = 0.0, blk_gr = 0.0, blk_lim = 1.0;

	for (unsigned long i = 0; i < n; i++) {
		const double ig = in_start + in_inc * (double)(i + 1);
		const double dry_l = inL[i], dry_r = inR[i]; /* outL may alias inL */
		double l = dry_l * ig, r = dry_r * ig;

		/* EQ: identical coefficients per channel keep the image stable */
		for (int b = 0; b < EQ_N; b++) {
			l = bq_run(&k->eq[b], &k->eqs[b][0], l);
			r = bq_run(&k->eq[b], &k->eqs[b][1], r);
		}

		/* mid/side balance: dialog usually lives in the mid (center) */
		double m = 0.5 * (l + r) * mid_g, s = 0.5 * (l - r) * side_g;
		l = m + s; r = m - s;

		double pk = fabs(l) > fabs(r) ? fabs(l) : fabs(r);
		if (pk > blk_peak) blk_peak = pk;

		/* leveler: slow gated AGC towards a mean (RMS) target */
		if (lev_idle) {
			/* identity */
		} else if (lev_on) {
			double p = 0.5 * (l * l + r * r);
			double pdb = lin2db(sqrt(p));
			if (pdb > lev_gate) /* freeze in silence: no noise pumping */
				k->lev_power = lev_pc * k->lev_power + (1.0 - lev_pc) * p;
			double want = lev_target - lin2db(sqrt(k->lev_power + DENORM));
			if (k->lev_power <= 0.0) want = 0.0;
			/* peak sensing: a loud peak asks for a cut right away, not after the RMS settles */
			double apk = fabs(l) > fabs(r) ? fabs(l) : fabs(r);
			k->lev_peak = apk > k->lev_peak ? apk : peak_rel * k->lev_peak + (1.0 - peak_rel) * apk;
			if (pdb > lev_gate) {
				double want_pk = lev_target + peak_room_db - lin2db(k->lev_peak);
				if (want_pk < want) want = want_pk;
			}
			if (want > lev_boost) want = lev_boost;
			if (want < -lev_cut) want = -lev_cut;
			double gc = want < k->lev_gain_db ? lev_gc_cut : lev_gc;
			k->lev_gain_db = gc * k->lev_gain_db + (1.0 - gc) * want;
		} else {
			k->lev_gain_db *= lev_gc; /* glide back to 0 dB */
		}
		if (!lev_idle) {
			double lg = db2lin(k->lev_gain_db);
			l *= lg; r *= lg;
		}

		/* compressor: peak-ish detector in dB, soft knee, stereo-linked */
		double x = fabs(l) > fabs(r) ? fabs(l) : fabs(r);
		const double mk = mk_start + mk_inc * (double)(i + 1);
		double xdb = lin2db(x);
		double c = xdb > k->comp_env_db ? att : rel;
		k->comp_env_db = c * k->comp_env_db + (1.0 - c) * xdb;
		double cg = mk;
		if (!comp_idle) {
			double gr = comp_curve(k->comp_env_db, thr, ratio, knee);
			if (gr < blk_gr) blk_gr = gr;
			cg = db2lin(gr) * mk;
		}
		l *= cg; r *= cg;

		/* look-ahead limiter: the gain needed for this sample is known
		 * LOOKAHEAD_MS before it reaches the output */
		double a = fabs(l) > fabs(r) ? fabs(l) : fabs(r);
		float needed = a > ceil_lin ? (float)(ceil_lin / a) : 1.0f;
		unsigned pos = k->la_pos;
		float dl = k->delay[0][pos], dr = k->delay[1][pos];
		k->delay[0][pos] = (float)l;
		k->delay[1][pos] = (float)r;
		k->la_pos = (pos + 1) % la;
#ifdef CINEGAIN_REFERENCE
		k->need[pos] = needed;
		float target = 1.0f;
		for (unsigned j = 0; j < la; j++)
			if (k->need[j] < target) target = k->need[j];
#else
		/* Same value as the scan: the minimum of the last la required gains. Every
		 * value is <= 1, so the 1.0 the scan starts from never wins; ties keep the
		 * newest entry, which expires last. O(1) amortized per sample. */
		const uint32_t t = k->lim_t++;
		if (k->dq_len && t - k->dq_idx[k->dq_head] >= la) {
			k->dq_head = (k->dq_head + 1) & DQ_MASK;
			k->dq_len--;
		}
		while (k->dq_len && k->dq_val[(k->dq_head + k->dq_len - 1) & DQ_MASK] >= needed)
			k->dq_len--;
		const unsigned back = (k->dq_head + k->dq_len) & DQ_MASK;
		k->dq_val[back] = needed;
		k->dq_idx[back] = t;
		k->dq_len++;
		const float target = k->dq_val[k->dq_head];
#endif
		double gcoef = target < k->lim_gain ? lim_att : lim_rel;
		k->lim_gain = gcoef * k->lim_gain + (1.0 - gcoef) * target;
		if (k->lim_gain > target && gcoef == lim_att && target < 1.0f) {
			/* never lag behind the requirement by more than the glide */
			if (k->lim_gain > target * 1.5) k->lim_gain = target * 1.5;
		}
		if (k->lim_gain < blk_lim) blk_lim = k->lim_gain;
		double ol = dl * k->lim_gain, orr = dr * k->lim_gain;

		/* safety: soft clip only above the ceiling, hard stop at 0 dBFS */
		if (fabs(ol) > ceil_lin) ol = (ol > 0 ? 1 : -1) * (ceil_lin + (1.0 - ceil_lin) * tanh((fabs(ol) - ceil_lin) / (1.0 - ceil_lin + 1e-9)));
		if (fabs(orr) > ceil_lin) orr = (orr > 0 ? 1 : -1) * (ceil_lin + (1.0 - ceil_lin) * tanh((fabs(orr) - ceil_lin) / (1.0 - ceil_lin + 1e-9)));
		if (k->xf_on) {
			double w = k->xf_pos < la ? 0.0 : (double)(k->xf_pos - la + 1) / (double)la;
			ol = (1.0 - w) * dry_l + w * ol;
			orr = (1.0 - w) * dry_r + w * orr;
			if (++k->xf_pos >= 2 * la) k->xf_on = 0;
		}
		if (ol > 1.0) ol = 1.0; else if (ol < -1.0) ol = -1.0;
		if (orr > 1.0) orr = 1.0; else if (orr < -1.0) orr = -1.0;
		outL[i] = (float)ol;
		outR[i] = (float)orr;
	}


	if (k->shm) {
		k->shm->level_db = (float)lin2db(blk_peak);
		k->shm->leveler_db = (float)k->lev_gain_db;
		k->shm->gr_db = (float)blk_gr;
		k->shm->limit_db = (float)lin2db(blk_lim);
		k->shm->ramp_db = (float)ramp_left;
		__atomic_store_n(&k->shm->seq, k->shm->seq + 1, __ATOMIC_RELEASE);
	}
	if (k->port[P_METER_LEVEL_DB]) *k->port[P_METER_LEVEL_DB] = (float)lin2db(blk_peak);
	if (k->port[P_METER_LEVELER_DB]) *k->port[P_METER_LEVELER_DB] = (float)k->lev_gain_db;
	if (k->port[P_METER_GR_DB]) *k->port[P_METER_GR_DB] = (float)blk_gr;
	if (k->port[P_METER_LIMIT_DB]) *k->port[P_METER_LIMIT_DB] = (float)lin2db(blk_lim);
}

static void cleanup(LADSPA_Handle h)
{
	Cinegain *k = h;
	if (k->shm) munmap(k->shm, sizeof(meter_shm));
	if (k->shm_path[0]) {
		/* only while the name still points at our file: a newer instance of this
		 * process may have replaced it (small race window between stat and unlink) */
		struct stat st;
		if (stat(k->shm_path, &st) == 0 && st.st_dev == k->shm_dev && st.st_ino == k->shm_ino)
			unlink(k->shm_path);
	}
	free(k);
}

static const LADSPA_Descriptor descriptor = {
	.UniqueID = 0x4B4E5431, /* "KNT1", private range */
	.Label = "cinegain",
	.Properties = LADSPA_PROPERTY_HARD_RT_CAPABLE,
	.Name = "Cinegain film voice processor",
	.Maker = "Tobias Moos <tobias@roteson.de>",
	.Copyright = "AGPLv3",
	.PortCount = P_COUNT,
	.PortDescriptors = port_desc,
	.PortNames = port_names,
	.PortRangeHints = port_hints,
	.instantiate = instantiate,
	.connect_port = connect_port,
	.activate = activate,
	.run = run,
	.cleanup = cleanup,
};

const LADSPA_Descriptor *ladspa_descriptor(unsigned long index)
{
	return index == 0 ? &descriptor : NULL;
}
