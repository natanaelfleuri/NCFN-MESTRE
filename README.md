# MESTRE

**Módulo Executivo de Sistemas, Tarefas e Recursos Eletrônicos** é um assistente de voz local para Linux, no estilo do JARVIS do Homem de Ferro. Ele conversa em português, controla o computador, navega na internet, cuida da agenda e trabalha sozinho em segundo plano. Na tela aparece um símbolo holográfico **NCFN** que reage à voz e às emoções.

![Interface do MESTRE](docs/interface.png)

O cérebro é o [Claude Code](https://docs.anthropic.com/claude-code). O MESTRE não tem modelo de linguagem próprio: ele conecta o Claude a três peças locais.
- **Ouvido:** reconhecimento de voz pelo Whisper, na GPU.
- **Voz:** fala neural em português com o Kokoro.
- **Interface:** a tela do assistente, com o símbolo NCFN.

Um **guardião** fica entre o cérebro e o computador e pede confirmação antes de qualquer ação perigosa.

---

## Sumário

- [O que ele faz](#o-que-ele-faz)
- [Como funciona](#como-funciona)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Uso](#uso)
- [Configurações](#configurações)
- [Segurança](#segurança)
- [Autonomia: agenda, tarefas e vigilância](#autonomia-agenda-tarefas-e-vigilância)
- [Uso online: janela suspensa num portal web](#uso-online-janela-suspensa-num-portal-web)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Personalização](#personalização)
- [Solução de problemas](#solução-de-problemas)
- [Privacidade](#privacidade)

---

## O que ele faz

| Área | Exemplos de pedido |
|---|---|
| Conversa | "O que você acha desse plano?", "Me explique como funciona um túnel da Cloudflare." |
| Computador | "Abra o YouTube", "Aumente o volume", "O que tem na minha tela?", "Copie esse texto." |
| Internet | "Quais são as notícias de hoje?", "Pesquise o preço do RTX 5070 e compare três lojas." |
| Arquivos e sistema | "Quanto espaço livre tenho?", "Liste os containers parados", "Crie um relatório em PDF sobre…" |
| Conta Google (opcional) | "Tenho e-mails novos do banco?", "O que tenho na agenda amanhã?" |
| Notícias (NewsFlash) | "Quais as notícias dos meus feeds?", "Leia essa notícia sobre a Apple", "Tem algo sobre o Brasileirão?" |
| Agenda | "Me lembre às 15h de ligar para o cliente", "Todo dia útil às 8h, me dê um resumo do dia." |
| Segundo plano | "Pesquise a fundo X e me avise quando terminar"; a conversa continua livre enquanto isso. |
| Câmera (opcional) | "Olhe pela câmera e me diga o que vê." |
| Livros e documentos | Envie um PDF ou EPUB no **Leitor** e peça "leia para mim" ou "o que diz o capítulo 3?" |
| Memória | "Anote que prefiro respostas curtas de manhã"; ele guarda no seu Obsidian e lembra depois. |

---

## Como funciona

```
 Microfone ──► Navegador (interface) ──WebSocket──► server.py ──► Whisper (GPU) ──► texto
                    ▲                                    │
                    │  áudio da fala + emoção            ▼
 Alto-falante ◄─────┴──────── Kokoro (voz) ◄── frases ◄── Claude Code (cérebro, stream-json)
                                                         │
                                       hooks/guard.py ◄──┤ cada ação passa pelo guardião
                                                         ▼
                      terminal · mestre-pc (tela, mouse, teclado, câmera, som)
                      navegador automático · Gmail/Agenda/Drive · agenda · tarefas
```

1. **Ouvir:** a interface capta o microfone e detecta quando você fala.
   - Os modos de escuta são conversa livre, palavra de ativação ou apertar para falar.
   - O áudio vai em PCM de 16 kHz para o servidor.
   - O Whisper transcreve, localmente na GPU ou, se você escolher, pela API da OpenAI.
2. **Pensar:** o texto vai para um processo do **Claude Code** que fica sempre aberto e lembra a conversa.
   - A personalidade e as regras estão em `persona.md`.
   - O Claude usa as ferramentas que estiverem liberadas: terminal, navegador, conectores e os comandos `mestre-*`.
3. **Proteger:** antes de cada ação, o **guardião** (`hooks/guard.py`) decide se ela passa.
   - Ações perigosas são barradas até você confirmar.
   - Permissões desligadas são recusadas sem confirmação.
4. **Falar:** a resposta chega em fluxo e é dividida em frases.
   - Cada frase vira áudio no **Kokoro**, cerca de 0,2 s por frase na GPU, e já começa a tocar enquanto o resto é gerado.
   - O Claude marca a emoção de cada trecho (`[feliz]`, `[preocupado]`…), e o símbolo NCFN muda de comportamento exatamente quando aquela frase é falada.
5. **Agir sozinho:** em paralelo, o servidor cuida da agenda, das tarefas em segundo plano e da vigilância do sistema, e avisa por voz quando algo acontece.

---

## Requisitos

- **Linux com X11.** Testado no Zorin OS, que é baseado no Ubuntu 24.04. No Wayland, o controle de mouse, teclado e janelas não funciona.
- **Python 3.11 ou mais novo** e **Google Chrome**.
- **[Claude Code](https://docs.anthropic.com/claude-code) instalado e autenticado.** O comando `claude` precisa funcionar no terminal.
  - O uso conta no seu plano do Claude.
- **GPU NVIDIA**, recomendada. Sem ela tudo funciona na CPU, mas a fala e o reconhecimento ficam mais lentos.
- **Espaço em disco:** cerca de 8 GB para o ambiente Python (PyTorch e CUDA) e os modelos.

---

## Instalação

```bash
git clone https://github.com/natanaelfleuri/NCFN-MESTRE.git ~/mestre
cd ~/mestre
./install.sh
```

O instalador roda sem sudo e faz o seguinte:

1. Cria o ambiente Python em `.venv` e instala as dependências de `requirements.txt`.
2. Baixa a voz de reserva (Piper). O Whisper (cerca de 1,6 GB) e o Kokoro (cerca de 330 MB) baixam sozinhos no primeiro uso.
3. Cria o `config.json` a partir de `config.example.json`.
4. Instala:
   - o serviço `~/.config/systemd/user/mestre.service`;
   - o lançador `~/.local/bin/mestre`;
   - o ícone **MESTRE** na área de trabalho e no menu.

Para desinstalar, rode `./uninstall.sh`. Ele não apaga a pasta do projeto.

---

## Uso

Clique no ícone **MESTRE**, ou rode `mestre` no terminal. A janela abre em tela cheia e o MESTRE cumprimenta você.

| Ação | Como |
|---|---|
| Falar | No modo **Conversa livre**, basta falar. Também dá para segurar a **barra de espaço** (sem limite de tempo) ou o botão do microfone. |
| Digitar | Caixa "…ou digite um comando" |
| Falar pelo símbolo | **Segure o botão esquerdo** sobre o símbolo NCFN (solte para enviar) |
| Configurações pelo símbolo | **Botão direito** sobre o símbolo |
| Ligar e desligar a animação | **Rodinha do mouse** sobre o símbolo. Desligada, o símbolo fica parado e esmaecido e poupa a GPU; a escolha fica salva. |
| Interromper a fala | **Esc** ou ■ |
| Nova conversa | ⟲ |
| Configurações | Botão **⚙ Configurações** ou ⚙ no canto superior |
| Minimizar, maximizar ou fechar | Botões – ▢ ✕ no canto superior direito. O ✕ fecha e desliga o servidor. |
| Reabrir a janela minimizada | Clique no ícone MESTRE de novo |

### Modos de escuta

| Modo | Comportamento |
|---|---|
| **Conversa livre** (padrão) | Ouve e responde a tudo o que você falar. Corta a frase depois de 0,9 s de silêncio ou 30 s de fala. |
| **Com "Mestre"** | Só responde quando ouve "Mestre" na frase. Depois de cada resposta, aceita a continuação sem o nome por 12 s. |
| **Apertar p/ falar** | Só escuta com o espaço ou o microfone apertado. |

Enquanto o MESTRE fala, ele não escuta, para não ouvir a própria voz. Para interromper, use o espaço ou o Esc.

### Comandos de terminal

```bash
mestre            # abre, ou traz a janela para frente
mestre status     # ligado ou desligado
mestre stop       # fecha e desliga
mestre-pc help    # controle da área de trabalho (o cérebro usa este comando)
mestre-agenda listar
mestre-tarefa "pesquise X e salve um resumo"
```

---

## Configurações

![Configurações](docs/configuracoes.png)

| Seção | Opções |
|---|---|
| **Voz e símbolo** | Voz masculina (Alex, Santa ou Faber) com símbolo **angular**, ou feminina (Dora) com símbolo **fluido**. Controle de **tom** e botão **Testar voz**. |
| **Imagem** | Cor do holograma (automática, ciano, verde, âmbar, vermelho, roxo, rosa ou branco) e chuva de números ligada ou desligada |
| **Reconhecimento de voz** | Whisper local (padrão) ou **API da OpenAI** (`gpt-4o-transcribe`). A chave fica só em `state/secrets.json` (chmod 600). Se a OpenAI falhar, ele volta sozinho para o Whisper. |
| **Cérebro** | Opus (mais capaz) ou Sonnet (mais rápido) |
| **Memória (arquivos .md)** | Pasta onde o MESTRE guarda a memória em Markdown: escolher pelo seletor do sistema, digitar o caminho ou abrir a pasta |
| **Vigilância do sistema** | Ativa ou desligada |
| **Conexões e permissões** | Uma chave liga/desliga para cada capacidade (lista abaixo) |
| **Sempre permitidos** | Ações que você liberou de vez, com botão para revogar |
| **Agenda** | Lembretes e tarefas agendadas, com botão para remover |

### Conexões e permissões

| Grupo | Permissões |
|---|---|
| Conexões | Gmail, Google Agenda, Google Drive, Claude Docs, Cloudflare |
| Internet | Pesquisa na internet, navegador automático (Playwright), notícias (NewsFlash) |
| Dispositivos | **Câmera** (vem desligada), ver a tela, mouse e teclado, abrir programas e sites, área de transferência, som e mídia |
| Sistema | Terminal, criar e editar arquivos, Docker, áreas protegidas |
| Autonomia | Agenda e lembretes, tarefas em segundo plano, memória da conversa |

- **Conexões:** são os conectores da sua conta Claude. O status aparece ao lado de cada uma: conectado, precisa entrar ou falhou.
- **Conectar:** o botão pede ao MESTRE que abra a página de login do conector.
- **Gerenciar no claude.ai:** abre a página de conectores da sua conta.

O que está **desligado** some do cérebro. Ferramentas inteiras ficam fora do Claude, e comandos específicos são barrados pelo guardião. O MESTRE avisa que a permissão está desligada e não tenta outro caminho.

---

## Segurança

O MESTRE roda o Claude Code com acesso ao seu computador. Por isso toda ação passa pelo guardião (`hooks/guard.py`), um gancho `PreToolUse` do Claude Code:

- **Liberadas sem pergunta:** leitura, consultas, navegação, abrir programas e escrever em `~/Documentos/MESTRE`.
- **Pedem confirmação:**
  - apagar ou mover arquivos, sobrescrever com `>`;
  - `sudo`, instalar ou remover programas, `systemctl`;
  - `docker rm/stop/down/exec`, `git push/reset`, `crontab`;
  - enviar dados (`curl -X POST`, `scp`, `ssh`, e-mail);
  - ler segredos (`.env`, chaves SSH, `credentials`…);
  - mexer nas **áreas protegidas** (`protected_paths` no `config.json`);
  - alterar arquivos fora da pasta do MESTRE;
  - conectores que agem para fora (enviar e-mail, criar evento…).
- **A confirmação** abre uma caixa com **Confirmar sempre**, **Só desta vez** e **Negar**. Também vale por voz: "confirmo", "confirmo sempre", "cancela".
  - A aprovação vale para **aquela chamada exata**.
  - "Sempre" grava a regra em `state/always.json`, revogável nas Configurações.
- **Tarefas autônomas** (agenda e segundo plano) não têm quem confirme, então nelas toda ação protegida é **negada** e aparece como pendência no relatório.
- **Permissões desligadas** são recusadas sem oferecer confirmação.

> O guardião é uma camada de proteção, não uma garantia absoluta. Ele bloqueia padrões perigosos conhecidos. Revise as permissões e não deixe o MESTRE em "conversa livre" num ambiente com outras pessoas falando, como TV ou reuniões.

---

## Autonomia: agenda, tarefas e vigilância

- **Lembretes:** na hora marcada, o MESTRE **fala** o lembrete e mostra uma notificação.
  ```bash
  mestre-agenda lembrete "15:30" "ligar para o cliente"
  mestre-agenda lembrete "+20m" "tirar o café"
  ```
- **Tarefas agendadas:** na hora marcada, ele executa o pedido sozinho e fala o resultado.
  ```bash
  mestre-agenda tarefa "2026-11-03 08:00" "resumo das notícias e do estado dos meus serviços" --repetir dias-uteis
  ```
  As repetições possíveis são `diario`, `dias-uteis`, `semanal`, `horas:N` e `minutos:N`.
- **Segundo plano:** `mestre-tarefa "…"` executa um trabalho longo num processo separado. Quando termina, ele fala o resumo e salva um relatório em `~/Documentos/MESTRE/relatorios/`.
- **Vigilância** (a cada minuto): avisa por voz e por notificação, uma vez por problema, quando:
  - o disco passa de 90%;
  - a GPU passa de 85 °C ou o processador de 95 °C;
  - a memória passa de 92%;
  - a bateria fica fraca com o carregador desligado;
  - um **container Docker para**;
  - um endereço de `watch_urls` deixa de responder.

Na conversa, basta pedir: "me lembre…", "todo dia às…", "faça isso em segundo plano".

---

## Leitor: notícias e biblioteca

O botão **📖 Leitor** abre um painel com três abas:

- **Notícias:** as manchetes do NewsFlash separadas por **categoria** (Todas, Tecnologia, Esportes…).
  - Cada manchete tem **▶ Ler resumo**, **▶ Matéria completa** (o MESTRE baixa o texto do site) e **↗ Abrir**.
  - **Ler resumos desta categoria** lê a categoria em sequência.
  - **Resumo pelo MESTRE** pede à IA as cinco mais importantes.
- **Biblioteca:** envie **PDF, EPUB, DOCX, TXT, MD ou HTML**, até 200 MB, clicando ou arrastando o arquivo.
  - O texto é extraído e guardado em `~/Documentos/MESTRE/biblioteca/`.
  - Cada documento tem **▶ Ler / Continuar** (a posição fica salva), **⏮ Do início**, **↷ Ir para página**, **✦ Perguntar à IA** (resumo ou perguntas sobre o conteúdo) e **✕ Remover**.
- **Ajustes da leitura:** velocidade (0,6× a 1,6×), voz (pode ser outra, diferente da do MESTRE), tom, pausa entre parágrafos e mostrar ou não o trecho que está sendo lido.

**Controles durante a leitura:** ⏮ ⏸/▶ ⏹ ⏭, na barra embaixo do símbolo e dentro do painel. Segurar o espaço para falar com o MESTRE para a leitura, e o ▶ retoma de onde parou. PDFs escaneados como imagem não têm texto para extrair.

---

## Tela dividida

Quando o MESTRE abre uma página (`mestre-pc open https://…`), ela abre no seu navegador padrão **dividindo a tela**:

- **Primeira página:** janela nova do navegador nos **2/3** do monitor onde o MESTRE está, e o MESTRE em **1/3**, com um layout estreito (símbolo em cima, conversa embaixo).
- **Páginas seguintes:** abrem como **abas** na mesma janela.
- **Ao fechar essa janela:** o MESTRE volta sozinho para a tela cheia. Também dá para pedir `mestre-pc unsplit`.

Funciona com Brave, Chrome, Chromium, Firefox, Vivaldi e Edge. O navegador automático (Playwright) continua numa janela própria.

---

## Memória no Obsidian

Em **⚙ Configurações → Memória (arquivos .md)**, escolha uma pasta do computador. O botão **📁 Escolher pasta…** abre o seletor do sistema; também dá para digitar o caminho e clicar em Salvar. **Abrir pasta** mostra os arquivos. Pode ser uma pasta do seu cofre do Obsidian ou qualquer outra, e ela fica gravada em `memory_dir` no `config.json`.

- **Pasta nova:** é criada com as notas iniciais (índice, sobre você e diário). Se já tiver notas, nada é sobrescrito.
- **Função:** a pasta vira a **memória de longo prazo** do MESTRE:

- **O que ele guarda sozinho, sem pedir licença:** preferências, fatos sobre você e seus projetos, decisões, pendências e resultados de pesquisas.
  - Cada assunto vira uma nota Markdown com frontmatter (`tags`, `criado`, `atualizado`) e links `[[…]]`.
  - O índice fica em `00 - ÍNDICE.md`, os fatos sobre você em `SOBRE O USUÁRIO.md` e o diário das conversas relevantes em `CONVERSAS.md`.
- **Ao iniciar:** ele lê o índice, a nota sobre você e o fim do diário.
- **Guardião:** só essa pasta fica liberada para escrita. O resto do cofre continua protegido, e apagar continua pedindo confirmação.
- **Nunca guarda** senhas, tokens ou chaves.
- **Desligar:** a permissão **Memória no Obsidian**.

Você pode ler, corrigir ou apagar as notas quando quiser; ele segue a versão corrigida.

---

## Notícias (NewsFlash)

Se o leitor de RSS [NewsFlash](https://flathub.org/apps/io.gitlab.news_flash.NewsFlash) estiver instalado (Flatpak ou nativo), o MESTRE usa **as suas assinaturas e categorias** dele:

- **Faixa de manchetes:** passa embaixo do símbolo, atualizada a cada 10 minutos. Clicar numa manchete faz o MESTRE ler e resumir a notícia.
- **Por voz:** "quais as notícias?", "notícias de esportes", "leia essa", "tem algo sobre X?".
- **Comando `mestre-noticias`:**
  ```bash
  mestre-noticias manchetes [--categoria TECNOLOGIA] [--fonte X] [--n 15]
  mestre-noticias feeds
  mestre-noticias buscar "termo"
  mestre-noticias ler <url>
  mestre-noticias abrir        # abre o aplicativo NewsFlash
  ```
- **Origem das notícias:** as manchetes vêm **ao vivo** dos feeds, então não dependem de o NewsFlash ter sincronizado. Sem internet, ele usa as notícias guardadas pelo NewsFlash.
- **Banco do NewsFlash:** é aberto **só para leitura**. Para assinar ou remover feeds, use o próprio NewsFlash.
- **Desligar:** em Configurações, a permissão **Notícias (NewsFlash)** e a opção **Faixa de notícias**.

---

## Uso online: janela suspensa num portal web

O MESTRE pode atender dentro de um portal web seu (no caso do autor, o portal
NCFN), de qualquer lugar: um botão no menu do portal (ou o orbe no canto, ou
**Alt+M**) abre uma janela suspensa, arrastável e redimensionável, onde você
escreve ou fala os comandos. Ali ele lê as páginas, navega, clica, preenche
formulários e consulta a API do portal — sempre no seu navegador, com a sua
sessão e as suas permissões.

Como funciona:

```
navegador (portal)  ──wss /mestre-link/portal/ws──▶  Cloudflare Access ─▶ túnel ─▶ Caddy
                                                                                  │
         ◀── comandos ncfn-tela (ler, navegar, clicar…) ──  MESTRE (gateway da rede Docker)
                                                              └─ cérebro "portal" (só ncfn-tela)
```

- **Ouvinte separado.** Além de `127.0.0.1`, o servidor escuta só no gateway
  da rede Docker do portal (`portal.docker_network`), nunca em `0.0.0.0`.
  Nesse ouvinte só existe `/portal/*`; a interface local, as APIs e o `/ws`
  ficam inacessíveis por ele.
- **Autenticação.** A conexão precisa do token do Cloudflare Access
  (`Cf-Access-Jwt-Assertion`): assinatura RS256 conferida nas chaves do time,
  AUD da aplicação, emissor e e-mail na lista `allowed_emails`, além da origem
  do portal. Sem configuração completa, recusa tudo.
- **Cérebro próprio.** Um segundo processo `claude -p` com sessão própria
  (`state/session-portal.json`), persona `persona_portal.md` (+
  `persona_portal.local.md` com o mapa das rotas, fora do git), só a
  ferramenta Bash, sem conectores MCP. O guardião, no perfil `portal`, só
  aceita `ncfn-tela …` sem `;`, `&&`, `|`, `>`, `$` ou crases: ele não toca
  no seu computador.
- **`bin/ncfn-tela`** manda o comando ao servidor (`POST /api/portal/cmd`, só
  local), que o repassa à janela aberta e devolve o resultado:
  `ler [tudo]`, `navegar /rota`, `voltar`, `recarregar`, `clicar "texto" [n]`,
  `preencher "campo" "valor"`, `enviar ["campo"]`, `rolar baixo|cima|topo|fim`,
  `api MÉTODO /api/rota ['json']`.
- **Confirmação na janela.** `api` com POST/PUT/PATCH/DELETE e cliques em
  botões como Excluir, Apagar, Revogar, Destruir ou Bloquear abrem a caixa
  *Só dessa vez / Confirmar sempre / Negar* antes de acontecer.
- **Voz.** O botão 🎙 grava e manda o áudio para o Whisper do PC; o 🔊 liga as
  respostas faladas (Kokoro), tocadas pela Web Audio API.
- **Sempre disponível.** Com o portal ligado, o ✕ da janela do PC só fecha a
  janela (`mestre fechar`); o servidor continua atendendo o portal. `mestre
  stop` desliga tudo.

Configuração (`config.json`, bloco `portal`):

```json
"portal": {
  "enabled": true,
  "docker_network": "nome_da_rede_do_portal",
  "bind": "auto",
  "cf_team_domain": "https://SEU-TIME.cloudflareaccess.com",
  "cf_aud": ["AUD-DA-APLICACAO"],
  "allowed_emails": ["voce@exemplo.com"],
  "origins": ["https://portal.exemplo.com"],
  "keep_alive": true
}
```

No proxy do portal (Caddy), a rota que leva ao MESTRE:

```
handle_path /mestre-link/* {
    @sem_access not header Cf-Access-Jwt-Assertion *
    respond @sem_access 403
    reverse_proxy IP_DO_GATEWAY:7777
}
```

O componente da janela (React/Next.js) fica no repositório do portal; ele
conecta em `/mestre-link/portal/ws` (ou direto em `ws://127.0.0.1:7777/portal/ws`
quando o portal é aberto em localhost) e implementa os comandos acima no
navegador. O cabeçalho `Permissions-Policy` do portal precisa permitir
`microphone=(self)` para o 🎙 funcionar.

Proteções gerais: o servidor local recusa `POST` e WebSocket vindos de outras
origens (um site aberto no navegador não consegue mandar ordens ao MESTRE).

---

## Estrutura do projeto

```
server.py            servidor FastAPI: WebSocket, ouvido, voz, cérebro, agenda, vigilância
persona.md           personalidade e regras do MESTRE (prompt do sistema)
persona.local.md     contexto pessoal opcional (ignorado pelo git)
permissions.py       catálogo de permissões (ferramentas e padrões de comando)
hooks/guard.py       guardião: confirmação, "sempre permitidos" e permissões
bin/mestre-pc        tela, mouse, teclado, janelas, câmera, área de transferência, volume
bin/mestre-agenda    lembretes e tarefas agendadas
bin/mestre-tarefa    tarefas em segundo plano
bin/mestre-noticias  notícias dos feeds do NewsFlash
bin/ncfn-tela        ações na página do portal aberta na janela suspensa
portal.py            ponte online: ouvinte na rede Docker, token do Cloudflare Access, repasse
persona_portal.md    persona do cérebro que atende dentro do portal
news.py              leitura do NewsFlash e dos feeds RSS/Atom
reader.py            biblioteca: extração de PDF/EPUB/DOCX/TXT e trechos para leitura
ui/                  interface: index.html, style.css, app.js, symbol.js (símbolo NCFN)
install/             modelos do serviço systemd, do lançador e do atalho
config.example.json  configuração inicial (vira config.json)
state/               chaves, sessão, permissões e agenda (ignorado pelo git)
models/              modelos de voz baixados (ignorado pelo git)
```

### Protocolo da interface (`/ws`)

| Direção | Mensagem |
|---|---|
| interface → servidor | áudio PCM int16 16 kHz (binário); `{type:'text'}`, `decision`, `stop`, `reset`, `greet`, `test_voice` |
| leitor (interface → servidor) | `reader_play`, `reader_need`, `reader_pos`, `reader_seek`, `reader_stop` |
| servidor → interface | `reader_start`, `reader_say` (trecho em áudio + texto + página), `reader_end_queued`, `library`, `status`, `user`, `delta` (texto da resposta), `say` (áudio WAV + emoção), `emotion`, `tool`, `pending`, `approved`, `denied`, `task`, `reminder`, `alert`, `agenda`, `settings` |

APIs HTTP:
- `GET /api/stats` e `GET/POST /api/settings`;
- `GET /api/connections` e `POST /api/connect/{id}`;
- `GET/POST/DELETE /api/agenda` e `POST /api/task`;
- `GET /api/news` (manchetes do NewsFlash);
- `GET /api/library`, `POST /api/library/upload` (corpo = arquivo, cabeçalho `X-Filename`), `DELETE /api/library/{id}`, `POST /api/library/{id}/ask`;
- `GET/POST /api/reader/settings`;
- `POST /api/window/{minimize|maximize|close}`;
- `POST /api/portal/cmd` (só local, usado pelo `ncfn-tela`) e `WS /portal/ws` (janela do portal: `page`, `text`, áudio, `voice`, `cmd_result`, `stop`, `reset` ↔ `hello`, `status`, `user`, `delta`, `tool`, `say`, `cmd`, `done`).

---

## Personalização

- **Personalidade:** edite `persona.md`. Para dar ao MESTRE contexto sobre você (profissão, projetos, máquinas) sem publicar nada, crie `persona.local.md`.
- **Memória:** em `config.json`, `"memory_dir": "~/Documentos/Obsidian/MEMÓRIA DO ASSISTENTE"`.
- **Áreas protegidas:** em `config.json`, `"protected_paths": ["~/projetos/importante", "COFRE"]`.
- **Endereços vigiados:** em `config.json`, `"watch_urls": {"meu site": "http://127.0.0.1:8080/"}`.
- **Vozes:** em `config.json`, na chave `voices`.
  - Vozes do Kokoro em português: `pm_alex`, `pm_santa`, `pf_dora`.
  - `pitch` abaixo de 1 deixa a voz mais grave.
- **Símbolo:** o visual fica em `ui/symbol.js`.
  - `MOODS` define o comportamento de cada emoção.
  - A variante `angular` ou `fluida` acompanha a voz escolhida.
- **Porta:** 7777, no lançador e no serviço.

Depois de mudar `server.py` ou `persona.md`, rode `systemctl --user restart mestre`.

---

## Solução de problemas

| Problema | Solução |
|---|---|
| "Sem conexão" na tela | O servidor não está rodando. Veja `systemctl --user status mestre` e `journalctl --user -u mestre -n 50`. |
| Não reage à voz | Confira o modo de escuta. Em **Com "Mestre"**, frases sem o nome aparecem no painel como ignoradas. Teste segurando o espaço. |
| Ouve frases que ninguém disse | Ruído de fundo. Use **Com "Mestre"** ou **Apertar p/ falar**. |
| Minimizar, maximizar ou ver a tela não funciona | O serviço precisa da tela da sessão. O lançador roda `systemctl --user import-environment DISPLAY XAUTHORITY`. Abra sempre pelo ícone ou por `mestre`. |
| Voz robótica | O Kokoro não carregou e ele usou o Piper de reserva. Veja os logs. Na primeira vez, o download leva alguns minutos. |
| Conector "precisa entrar" | Clique em **Conectar** nas Configurações e faça o login na página que abrir. |
| OpenAI falha | A API da OpenAI cobra à parte da assinatura do ChatGPT. Sem crédito, o MESTRE volta sozinho para o Whisper local. |

---

## Privacidade

- **Ouvido e voz:** rodam **localmente**. O áudio só sai do computador se você escolher a API da OpenAI.
- **Cérebro:** o texto das conversas vai para a Anthropic, porque o cérebro é o Claude Code. Vale a mesma política da sua conta Claude.
- **Chaves e estado local:** chaves de API, permissões, sessão e agenda ficam em `state/`. O perfil do Chrome do app também. Nada disso vai para o git.
- **Fotos e capturas:** a câmera vem desligada. Capturas de tela e fotos são salvas só em `/tmp` e lidas pelo cérebro quando você pede.

---

Projeto NCFN.
