; Instalador do Tarjador Desktop — Edição Leve (Inno Setup 6).
;
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" desktop\tarjador.iss
;
; Pressupõe o bundle já em desktop\dist\Tarjador (ver desktop\tarjador.spec).
;
; Escolhas feitas pensando em máquina gerenciada:
;   * {autopf}: instala em Program Files quando roda como admin — diretório
;     não-gravável pelo usuário, que é o que a política padrão do AppLocker
;     permite executar. Sem admin, cai para %LOCALAPPDATA% em vez de falhar.
;   * Sem regra de firewall: o app escuta em 127.0.0.1 (ver run_tarjador.py),
;     então o Windows não pede exceção — nada de UAC no primeiro uso.
;   * /VERYSILENT funciona de fábrica, o que permite empacotar para
;     Intune/SCCM sem alterar nada aqui.

; Edição: compile com  ISCC /DEdition=full tarjador.iss  para a Completa.
; Sem o define, sai a Leve. MESMO AppId nas duas: instalar a Completa por
; cima da Leve É o upgrade (e vice-versa o downgrade) — nunca as duas lado a
; lado, que duplicaria 580 MB+ e confundiria qual atalho abre o quê.
#ifndef Edition
  #define Edition "lite"
#endif
; A versão vem de desktop/VERSION, repassada pelo build.ps1 em /DVersaoBase —
; fonte única (ver o comentário em tarjador.spec). O default só existe para
; compilar o .iss à mão sem quebrar.
#ifndef VersaoBase
  #define VersaoBase "0.0.0-dev"
#endif
#define MyAppName "Tarjador"
#define MyAppVersion VersaoBase + "-" + Edition
#define MyAppPublisher "Projeto Tarjador"
#define MyAppExeName "Tarjador.exe"

[Setup]
; AppId identifica o produto entre versões — NÃO mudar, senão cada versão
; instala ao lado da anterior em vez de atualizá-la.
AppId={{8F3A1C42-5B7E-4D6A-9E21-7A4C0B93D5E1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=TarjadorSetup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Admin quando disponível (instalação por máquina); o usuário pode escolher
; instalar só para si se não tiver privilégio.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayName={#MyAppName} {#MyAppVersion}
; Fecha um Tarjador aberto antes de instalar/desinstalar. Sem isso, o
; desinstalador não remove o que está em uso e deixa ~50 MB para trás em
; Program Files (aconteceu no primeiro teste real); e uma atualização por
; cima falharia em substituir o executável. `force` porque o app não tem
; janela própria que responda a WM_CLOSE — a UI é uma aba de navegador.
CloseApplications=force
; Cadeado também no Setup.exe e em Programas e Recursos (os atalhos herdam o
; ícone embutido no Tarjador.exe pelo PyInstaller).
SetupIconFile=assets\tarjador.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
; A AGPL-3.0 aparece como página do assistente. O arquivo é o LICENSE verbatim
; da raiz (ASCII puro — por isso não precisa de BOM, ao contrário dos .iss/.ps1
; deste projeto). Institucionalmente importa: quem instala vê sob que termos.
LicenseFile=..\LICENSE
; Os créditos de terceiros NÃO viram página do assistente: são 96 pacotes, uma
; parede de texto que ninguém lê em troca de mais um clique. A obrigação de
; atribuição (Apache-2.0, CC BY-SA) se cumpre com o THIRD-PARTY-NOTICES.txt
; instalado em {app}, que é a prática corrente.
WizardStyle=modern
; O bundle tem ~500 MB; avisa o wizard para calcular espaço direito.
DirExistsWarning=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "dist\Tarjador\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir o {#MyAppName} agora"; Flags: nowait postinstall skipifsilent

; Sem [UninstallDelete]: o log e o porta.txt vivem em
; %LOCALAPPDATA%\Tarjador, que é POR USUÁRIO. Numa desinstalação em modo
; administrativo, {localappdata} resolve para o perfil de quem desinstala —
; não para o de quem usou o app. Apagaria a pasta errada num caso e nada no
; outro, e o Inno avisa exatamente isso ("UsedUserAreasWarning").
;
; Ficam para trás dois arquivos de texto de poucos KB. É o preço certo: é
; melhor deixar um log do que apagar diretório de outro usuário por engano.
