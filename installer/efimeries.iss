; Εγκατάσταση «Εφημερίες Εκπαιδευτικών» (Inno Setup 6). Χτίζεται στο GitHub Actions:
;   iscc /DAppVersion=0.49 /DArch=x64 /DSourceDir=..\dist\EfimeriesPortable_x64 installer\efimeries.iss
#ifndef AppVersion
  #define AppVersion "0.0"
#endif
#ifndef Arch
  #define Arch "x64"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\EfimeriesPortable_x64"
#endif
#define ExeName "EfimeriesPortable_" + Arch + ".exe"

[Setup]
; Ίδιο AppId για x64 και x86: η μία έκδοση αντικαθιστά την άλλη (μία εγκατάσταση ανά υπολογιστή).
AppId={{B7E4C1D2-5A3F-4E89-9C61-2F0D7A8B3E15}
AppName=Εφημερίες Εκπαιδευτικών
AppVersion={#AppVersion}
AppVerName=Εφημερίες Εκπαιδευτικών {#AppVersion}
AppPublisher=Εφημερίες Εκπαιδευτικών
AppPublisherURL=https://github.com/stam1966/efimeries-ekpaideftikon
AppSupportURL=https://github.com/stam1966/efimeries-ekpaideftikon/issues
AppUpdatesURL=https://github.com/stam1966/efimeries-ekpaideftikon/releases
DefaultDirName={localappdata}\Programs\EfimeriesEkpaideftikon
DefaultGroupName=Εφημερίες Εκπαιδευτικών
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Efimeries_Setup_v{#AppVersion}_{#Arch}
SetupIconFile=..\app_icon.ico
UninstallDisplayIcon={app}\app_icon.ico
UninstallDisplayName=Εφημερίες Εκπαιδευτικών
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
MinVersion=6.1
CloseApplications=force
RestartApplications=no
#if Arch == "x64"
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#endif

[Languages]
#ifexist AddBackslash(SourcePath) + "Greek.isl"
Name: "el"; MessagesFile: "{#SourcePath}\Greek.isl"
#else
Name: "el"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "Εικονίδιο στην επιφάνεια εργασίας"; GroupDescription: "Εικονίδια:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "data\*,exports\*,backups\*,storage_config.json,update_state.json,create_shortcut.bat"; Flags: ignoreversion recursesubdirs createallsubdirs
; Σημαία «εγκατεστημένη εφαρμογή»: τα δεδομένα πηγαίνουν στα Έγγραφα και όχι στον φάκελο της εφαρμογής
Source: "installed.flag"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Εφημερίες Εκπαιδευτικών"; Filename: "{app}\{#ExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\app_icon.ico"
Name: "{group}\Οδηγός χρήσης"; Filename: "{app}\Odigos_Chrisis_Efimeries.pdf"
Name: "{autodesktop}\Εφημερίες Εκπαιδευτικών"; Filename: "{app}\{#ExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\app_icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ExeName}"; WorkingDir: "{app}"; Description: "Άνοιγμα της εφαρμογής"; Flags: nowait postinstall skipifsilent
; Ενημέρωση μέσα από την εφαρμογή: ξανανοίγει μόνη της μετά την εγκατάσταση (/LAUNCH=1)
Filename: "{app}\{#ExeName}"; WorkingDir: "{app}"; Flags: nowait; Check: LaunchRequested

[UninstallDelete]
; Τα δεδομένα (Έγγραφα\Εφημερίες Εκπαιδευτικών) δεν διαγράφονται ΠΟΤΕ από την απεγκατάσταση.
Type: files; Name: "{app}\update_state.json"
Type: files; Name: "{app}\storage_config.json"

[Messages]
el.FinishedLabel=Η εγκατάσταση ολοκληρώθηκε. Η εφαρμογή ανοίγει από το εικονίδιο «Εφημερίες Εκπαιδευτικών». Τα δεδομένα σας φυλάσσονται στα Έγγραφα, στον φάκελο «Εφημερίες Εκπαιδευτικών», και διατηρούνται σε ενημερώσεις και απεγκατάσταση.

[Code]
function LaunchRequested(): Boolean;
begin
  Result := ExpandConstant('{param:LAUNCH|0}') = '1';
end;
