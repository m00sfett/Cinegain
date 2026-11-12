Name:           cinegain
Version:        0.5.2
Release:        1%{?dist}
Summary:        Realtime volume booster, dialogue clarity optimizer & film audio processor for PipeWire

License:        AGPL-3.0-or-later
URL:            https://roteson.de
Source0:        https://github.com/m00sfett/cinegain/archive/refs/tags/v%{version}.tar.gz

BuildRequires:  gcc
Requires:       pipewire >= 0.3.50
Requires:       pipewire-pulseaudio
Requires:       python3 >= 3.10
Requires:       python3-pyqt6

%description
Cinegain is a dedicated realtime audio DSP processor designed for movies, TV
shows, laptops, and stream media on Linux. Features an intelligent dialogue
leveler, presence boost, center/side isolation, soft-knee compressor, and a
look-ahead peak limiter that keeps the output below full scale.

%prep
%autosetup

%build
gcc -O3 -fPIC -shared -Wall -Wextra src/cinegain.c -o src/cinegain.so -lm

%install
rm -rf $RPM_BUILD_ROOT
install -Dm755 src/cinegain.so %{buildroot}/usr/lib/ladspa/cinegain.so
install -Dm755 bin/cinegain %{buildroot}%{_bindir}/cinegain
install -Dm755 bin/cinegain-gui %{buildroot}%{_bindir}/cinegain-gui

install -d %{buildroot}%{_datadir}/cinegain/ui
install -m644 ui/*.py %{buildroot}%{_datadir}/cinegain/ui/
install -d %{buildroot}%{_datadir}/cinegain/config
install -m644 config/* %{buildroot}%{_datadir}/cinegain/config/
install -d %{buildroot}%{_datadir}/cinegain/presets
install -m644 presets/* %{buildroot}%{_datadir}/cinegain/presets/
install -d %{buildroot}%{_datadir}/cinegain/assets/icons
install -m644 assets/icons/* %{buildroot}%{_datadir}/cinegain/assets/icons/

install -Dm644 assets/cinegain.desktop %{buildroot}%{_datadir}/applications/cinegain.desktop
install -Dm644 assets/icons/cinegain.png %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/cinegain.png

%files
%license LICENSE
%doc README.md CHANGELOG.md
%{_bindir}/cinegain
%{_bindir}/cinegain-gui
/usr/lib/ladspa/cinegain.so
%{_datadir}/cinegain/
%{_datadir}/applications/cinegain.desktop
%{_datadir}/icons/hicolor/256x256/apps/cinegain.png

%changelog
* Thu Nov 12 2026 Tobias Moos <tobias@roteson.de> - 0.5.2-1
- Ear safety, hidden-volume reset that holds, full volume log, lower CPU load, UX polish

* Wed Nov 11 2026 Tobias Moos <tobias@roteson.de> - 0.5.1-1
- Release notes from CHANGELOG.md, SHA256SUMS asset, version ledger check before building

* Wed Nov 11 2026 Tobias Moos <tobias@roteson.de> - 0.5.0-1
- Volume first: scale -30..+15 dB, overdrive to +40 dB, gentle 10 dB/s rise, analysis of hidden volumes, view levels

* Wed Nov 11 2026 Tobias Moos <tobias@roteson.de> - 0.4.0-1
- Fixed and dynamic (beta) mode per program, hidden-volume guard, 6 user slots

* Wed Nov 11 2026 Tobias Moos <tobias@roteson.de> - 0.4.0-1
- Only one GUI or cinegain watch distributes streams

* Wed Nov 11 2026 Tobias Moos <tobias@roteson.de> - 0.4.0-1
- Debug log, filter off without pausing players, Chrome update keeps its setting

* Tue Nov 10 2026 Tobias Moos <tobias@roteson.de> - 0.3.4-1
- Compact start view, keyboard access, unknown status handling

* Tue Nov 10 2026 Tobias Moos <tobias@roteson.de> - 0.3.3-1
- Fix per-program leveler slider and cinegain route crash

* Tue Nov 10 2026 Tobias Moos <tobias@roteson.de> - 0.3.2-1
- Fix crash when clicking the roteson.de logo

* Tue Nov 10 2026 Tobias Moos <tobias@roteson.de> - 0.3.1-1
- Standard starts neutral; logos in the title banner

* Tue Nov 10 2026 Tobias Moos <tobias@roteson.de> - 0.3.0-1
- Program sidebar: own settings and on/off switch per program

* Tue Nov 10 2026 Tobias Moos <tobias@roteson.de> - 0.2.0-1
- Per-program profiles with permanent program list, factory reset

* Tue Nov 10 2026 Tobias Moos <tobias@roteson.de> - 0.1.1-1
- Fix Indonesian language code (id), correct limiter description

* Mon Nov 09 2026 Tobias Moos <tobias@roteson.de> - 0.1.0-1
- Initial public release v0.1.0
