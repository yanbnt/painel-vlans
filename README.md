# Painel de VLANs

Ferramenta local, no estilo do WinBox, para descobrir o que está ligado na rede, planejar VLANs e configurar switches gerenciáveis sem derrubar a rede.

Roda no próprio computador: um programa em Python serve a tela no navegador e conversa com o switch e com a placa de rede. Nada sai para a internet.

## O que faz

- **Equipamentos**
  - Procura tudo o que responde nas faixas em que o computador tem endereço e dá um palpite do tipo (switch, roteador, câmera, impressora, computador).
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

## Testes

```
python tests/test_painel.py
```

Os testes usam um switch simulado e gravações de rede montadas à mão. Eles não alteram o computador e nunca pedem autorização do Windows. A execução leva alguns minutos.

## O que já foi conferido

- **Em equipamento real**: a procura de equipamentos e a leitura das placas de rede.
- **Só em simulação**: aplicar blocos, o terminal e as listas ao vivo (switch simulado); as ações que pedem autorização do Windows.
- **Não conferido**: os comandos de MikroTik do assistente e do treino.
