# Painel de VLANs

Ferramenta local, no estilo do WinBox, para descobrir o que está ligado na rede, planejar VLANs e configurar switches gerenciáveis sem derrubar a rede.

Roda no próprio computador: um programa em Python serve a tela no navegador e conversa com o switch e com a placa de rede. Nada sai para a internet.

## O que faz

- **Equipamentos**
  - Procura tudo o que responde nas faixas em que o computador tem endereço e identifica cada equipamento sozinho: tipo, modelo, nome e fabricante.
  - A identificação usa só o que o equipamento responde sem senha: a tela web, o nome de rede dos computadores (NetBIOS), a descrição por SNMP (comunidade `public`), a descoberta de câmeras, gravadores e switches da linha Intelbras/Dahua, ONVIF, UPnP e o fabricante dono do MAC.
  - Soma endereços temporários à placa de rede para alcançar equipamentos que ainda estão com IP de fábrica.
  - Escuta os anúncios da rede (ARP, LLDP, CDP, MNDP, spanning tree, DHCP) para achar equipamentos em qualquer faixa de IP, endereços usados por dois equipamentos e servidores DHCP.
- **Projeto de VLANs**: plano por switch e por porta, comandos gerados e roteiro de migração.
- **Aplicar no switch** (Intelbras S2300G/S3300G, CLI no estilo Comware, por Telnet): envia só comandos de uma lista permitida, para no primeiro erro e recusa o bloco que cortaria a porta por onde o computador chega ao switch.
- **Listas ao vivo**: interfaces, VLANs, hosts, ARP, endereços e rotas do switch conectado.
- **Terminal**: explica cada comando e pede confirmação antes dos arriscados. Senhas digitadas nunca vão para o histórico.
- **Início rápido**: tarefas guiadas e um terminal de treino, para Intelbras e MikroTik.

## Como usar

Feito e testado no Windows 10 com Python 3.12. Usa só a biblioteca padrão do Python: não há nada para instalar.

1. Baixe esta pasta.
2. Dê dois cliques em `Iniciar painel.bat`.
3. O painel abre no navegador, em `http://localhost:8765`.

Também dá para iniciar pelo terminal:

```
python painel_local.py --porta 8765 --sem-navegador
```

## O que pede a sua autorização

Três ações precisam de permissão de administrador: somar um IP temporário, remover um IP temporário e escutar o cabo. Em cada uma o Windows mostra a própria janela de autorização. Sem a autorização, nada muda no computador.

- Os IPs temporários não substituem o endereço atual e somem quando o computador reinicia.
- A escuta usa o capturador que vem no Windows (`pktmon`), grava só anúncios da rede, nunca o tráfego normal, e apaga a gravação depois de ler.

## Arquivos que ficam só no seu computador

O programa cria estes arquivos ao lado dele. Eles guardam dados da sua rede, estão no `.gitignore` e não devem ir para o repositório:

| Arquivo | O que guarda |
|---|---|
| `plano.json`, `plano.anterior.json` | O plano de VLANs e a cópia da versão anterior |
| `historico.log` | O que o programa fez no switch e na placa de rede |
| `ips-temporarios.json` | Os IPs temporários que o painel somou |
| `escuta/` | O registro da última escuta |

## Tabela de fabricantes

O arquivo `fabricantes.tsv` liga o começo de cada MAC ao fabricante. Ele é gerado a partir da lista pública da IEEE. Para atualizar:

1. Baixe `oui.csv` de `https://standards-oui.ieee.org/oui/oui.csv` em uma pasta vazia.
2. Rode `python -I tools/gerar_fabricantes.py <caminho do oui.csv> fabricantes.tsv`.

Sem esse arquivo o painel funciona igual, só fica sem a coluna de fabricante.

## O que a procura não consegue ver

- Switch sem gerenciamento não tem endereço IP nem se anuncia na rede. Nenhuma procura o encontra.
- Switch gerenciável que ainda não recebeu um IP (alguns modelos saem de fábrica assim e pedem o primeiro acesso pela porta console) só aparece depois de configurado.

## Testes

```
python tests/test_painel.py
```

Os testes usam um switch simulado e gravações de rede montadas à mão. Eles não alteram o computador e nunca pedem autorização do Windows. A execução leva alguns minutos.

## O que já foi conferido

- **Em equipamento real**: a procura de equipamentos, a identificação automática (switches, câmeras, gravadores, impressoras, TVs e computadores) e a leitura das placas de rede.
- **Só em simulação**: aplicar blocos, o terminal e as listas ao vivo (switch simulado); as ações que pedem autorização do Windows.
- **Não conferido**: os comandos de MikroTik do assistente e do treino.
