Name:           cinegain
Version:        0.1.0
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
true-peak look-ahead limiter preventing digital clipping.

%prep
%autosetup

%build
gcc -O3 -fPIC -shared -Wall -Wextra src/cinegain.c -o src/cinegain.so -lm

%install
rm -rf $RPM_BUILD_ROOT
install -Dm755 src/cinegain.so %{buildroot}%{_libdir}/ladspa/cinegain.so
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
%{_libdir}/ladspa/cinegain.so
%{_datadir}/cinegain/
%{_datadir}/applications/cinegain.desktop
%{_datadir}/icons/hicolor/256x256/apps/cinegain.png

%changelog
* Sun Nov 09 2026 Tobias Moos <tobias@roteson.de> - 0.1.0-1
- Initial public release v0.1.0
