; Instalador do Painel de VLANs (Inno Setup 6.6 ou mais novo).
; Gere com installer\gerar-instalador.ps1: ele empacota o programa antes e passa a versão e a pasta do projeto.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef Raiz
  #define Raiz AddBackslash(SourcePath) + ".."
#endif
#define AppName "Painel de VLANs"
#define AppExe "PainelVLANs.exe"
#define Arte Raiz + "\build\arte"

[Setup]
AppId={{5B451A44-1846-47EC-9042-9F4B82AE08B0}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Instalador do {#AppName}
VersionInfoProductName={#AppName}
; Installs for the current user, without asking for administrator rights (the wizard lets them choose all users).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Modern look, light or dark like Windows.
WizardStyle=modern dynamic windows11 hidebevels
WizardSizePercent=110
WizardImageFile={#Arte}\lateral-202.png,{#Arte}\lateral-269.png,{#Arte}\lateral-336.png,{#Arte}\lateral-403.png,{#Arte}\lateral-430.png,{#Arte}\lateral-498.png
WizardSmallImageFile={#Arte}\pequena-58.png,{#Arte}\pequena-77.png,{#Arte}\pequena-97.png,{#Arte}\pequena-124.png
SetupIconFile={#Arte}\painel.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir={#Raiz}\build\saida
OutputBaseFilename=Instalar-Painel-de-VLANs-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
CloseApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Messages]
WelcomeLabel1=Bem-vindo ao {#AppName}
WelcomeLabel2=Este assistente vai instalar o [name/ver] neste computador.%n%nO painel encontra os equipamentos da rede, ajuda a planejar as VLANs por setor e configura switches gerenciáveis sem derrubar a rede.%n%nNão é preciso instalar o Python nem outro programa.
FinishedHeadingLabel=Tudo pronto
FinishedLabelNoIcons=O [name] foi instalado neste computador.
FinishedLabel=O [name] foi instalado neste computador. Ele fica no menu Iniciar e abre numa janela própria.%n%nSeus projetos ficam guardados na sua pasta de usuário e continuam lá quando o painel for atualizado ou removido.
ClickFinish=Clique em Concluir para sair.

[Tasks]
Name: "atalho"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; An update replaces the whole program folder; the user's data lives elsewhere.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#Raiz}\build\dist\PainelVLANs\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Equipamentos da rede, VLANs e switches"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Equipamentos da rede, VLANs e switches"; Tasks: atalho

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir o {#AppName} agora"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "EncerrarPainel"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Codigo: Integer;
begin
  { Closes the panel if it is open, so an update can replace its files. Nothing is lost: the data is saved as it changes. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
  Result := '';
end;
