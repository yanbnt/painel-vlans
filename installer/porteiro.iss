; Instalador do Porteiro (Inno Setup 6.6 ou mais novo).
; Gere com installer\gerar-instalador.ps1: ele empacota o programa antes e passa a versão e a pasta do projeto.

#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#ifndef Raiz
  #define Raiz AddBackslash(SourcePath) + ".."
#endif
#define AppName "Porteiro"
#define AppExe "Porteiro.exe"
; Version 1.0.0 was called Painel de VLANs: same AppId, so this installs over it as an update.
#define OldName "Painel de VLANs"
#define OldExe "PainelVLANs.exe"
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
OutputBaseFilename=Instalar-Porteiro-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
CloseApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Messages]
WelcomeLabel1=Bem-vindo ao {#AppName}
WelcomeLabel2=Este assistente vai instalar o [name/ver] neste computador.%n%nO Porteiro encontra e identifica os equipamentos da rede, organiza as portas em VLANs e configura os switches sem derrubar a rede.%n%nNão é preciso instalar o Python nem outro programa.
FinishedHeadingLabel=Tudo pronto
FinishedLabelNoIcons=O [name] foi instalado neste computador.
FinishedLabel=O [name] foi instalado neste computador. Ele fica no menu Iniciar e abre numa janela própria.%n%nSeus projetos ficam guardados na sua pasta de usuário e continuam lá quando o Porteiro for atualizado ou removido.
ClickFinish=Clique em Concluir para sair.

[Tasks]
Name: "atalho"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; An update replaces the whole program folder; the user's data lives elsewhere.
Type: filesandordirs; Name: "{app}\_internal"
; What version 1.0.0 left under its old name.
Type: files; Name: "{app}\{#OldExe}"
Type: files; Name: "{autoprograms}\{#OldName}.lnk"
Type: files; Name: "{autodesktop}\{#OldName}.lnk"

[Files]
Source: "{#Raiz}\build\dist\Porteiro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Gerenciamento de portas, VLANs e switches"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Gerenciamento de portas, VLANs e switches"; Tasks: atalho

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir o {#AppName} agora"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe} /IM {#OldExe}"; Flags: runhidden; RunOnceId: "EncerrarPorteiro"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Codigo: Integer;
begin
  { Closes the program if it is open (under the new or the old name), so an update can replace its files.
    Nothing is lost: the data is saved as it changes. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe} /IM {#OldExe}', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
  Result := '';
end;
