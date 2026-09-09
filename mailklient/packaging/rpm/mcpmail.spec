Name:           mcpmail
Version:        1.0.0
Release:        1%{?dist}
Summary:        Desktop email client with a unified inbox

# Placeholder only: the copyright holder has not selected a project license.
License:        LicenseRef-Unspecified
URL:            https://github.com/KamilMatyjaszczyk/mail-here
Source0:        %{name}-%{version}.tar.gz
Source1:        mailklient.desktop
Source2:        LICENSE-NOTICE
BuildArch:      noarch

# Replace the previously named RPM; both packages own the same Python module.
Provides:       mailklient = %{version}-%{release}
Obsoletes:      mailklient < 0.1.0-2

BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  desktop-file-utils
BuildRequires:  openssl

%description
A personal-alpha desktop email client for Linux using PySide6, IMAP/SMTP,
SQLite and the system keyring. Users supply their own OAuth app registration.

%prep
%autosetup
cp %{SOURCE2} LICENSE-NOTICE

%generate_buildrequires
%pyproject_buildrequires -x dev

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -L mailklient
desktop-file-install --dir=%{buildroot}%{_datadir}/applications %{SOURCE1}

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/mailklient.desktop
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
export XDG_DATA_HOME=$(mktemp -d)
export XDG_CONFIG_HOME=$(mktemp -d)
export PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring
%pytest

%files -f %{pyproject_files}
%doc README.md docs LICENSE-NOTICE
%{_bindir}/mcpMail
%{_bindir}/mcpMail-launcher
%{_bindir}/mailklient
%{_bindir}/mailklient-launcher
%{_datadir}/applications/mailklient.desktop

%changelog
* Wed Sep 09 2026 mcpMail contributors - 1.0.0-1
- Bump release version to 1.0.0.

* Tue Sep 08 2026 mcpMail contributors - 0.1.0-1
- Rename to mcpMail, preserving existing data and legacy commands.
