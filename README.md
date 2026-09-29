# Meal_Planner

Aplicação desktop em Python (Tkinter) para planear refeições. Gera receitas com o modelo local **Ollama (llama3.2)**, estima a nutrição através do **Open Food Facts**, apresenta fotografias dos pratos e pode criar playlists no **Spotify** com duração aproximada ao tempo de confeção, incluindo variantes Foo Fighters e Oasis.

## Funcionalidades

- Gerar duas versões da mesma refeição: mais saborosa e mais saudável
- Apresentar ingredientes, quantidades, porções, tempo e método de preparação
- Ajustar o número de porções e atualizar as quantidades
- Calcular uma estimativa nutricional por porção com o Open Food Facts
- Pesquisar e apresentar uma fotografia do prato
- Criar playlists Spotify com duração próxima do tempo de confeção
- Controlar a reprodução do Spotify: reproduzir, pausar, seguinte, anterior e shuffle
- Guardar localmente uma lista de itens em `notes.txt`

## Requisitos

- **Python 3** (testado com Python 3.13), com o comando `python` disponível no PowerShell
- **Pillow**, instalado pelo `requirements.txt`, para imagens na interface e capas de playlists
- **Tkinter**, incluído no instalador oficial do Python para Windows
- **Ollama**, a correr em `http://127.0.0.1:11434`, com o modelo **`llama3.2`** já descarregado
- Ligação à Internet para o Open Food Facts e para a pesquisa de fotografias
- *(Opcional)* Uma conta **Spotify Premium** para a integração Spotify completa

Se o computador ainda não tiver Python ou Ollama, siga a instalação pela ordem abaixo. Depois de instalar qualquer um deles, feche o PowerShell e abra uma janela nova, para o sistema passar a reconhecer os comandos.

## Instalação

### 1. Python

1. Descarregue o instalador em [python.org/downloads](https://www.python.org/downloads/).
2. Na primeira página do instalador, marque **Add python.exe to PATH** e só depois escolha **Install Now**.
3. Feche o PowerShell e abra outro.
4. Confirme a instalação:

   ```powershell
   python --version
   ```

   Deve aparecer uma versão 3.x. Se o comando não for reconhecido, o Python não ficou no PATH: volte a correr o instalador, escolha **Modify** e marque **Add python.exe to PATH**. Em algumas instalações de Windows o comando alternativo é `py -3`; se esse funcionar e `python` não, use `py -3` no lugar de `python` nos comandos seguintes.

O Tkinter vem com esse instalador. Se ao abrir a aplicação aparecer um erro `No module named 'tkinter'`, volte a executar o instalador, escolha **Modify** e confirme que **tcl/tk and IDLE** está selecionado.

### 2. Dependências Python

Abre o PowerShell na pasta do projeto e instala o Pillow:

```powershell
cd Meal_Planner
python -m pip install -r requirements.txt
```

Se o `cd Meal_Planner` falhar, o PowerShell está noutra pasta. Entre primeiro na pasta onde está este README, por exemplo:

```powershell
cd "C:\caminho\para\Meal_Planner"
```

### 3. Ollama

1. Descarregue e instale o Ollama em [ollama.com/download](https://ollama.com/download).
2. Feche o PowerShell e abra outro.
3. Confirme que o comando existe:

   ```powershell
   ollama --version
   ```

4. Descarregue o modelo usado pela aplicação. Este passo precisa de Internet e pode demorar:

   ```powershell
   ollama pull llama3.2
   ```

No Windows, o instalador deixa o Ollama a correr em segundo plano (ícone na área de notificação). Esse processo já escuta em `http://127.0.0.1:11434`, por isso não é preciso um terminal só para ele. Confirme que está disponível com:

```powershell
ollama list
```

Se o comando listar `llama3.2`, o serviço está a correr e pode avançar para a execução com um único terminal.

Se `ollama list` falhar porque não há ligação a `127.0.0.1:11434`, abra a aplicação Ollama a partir do menu Iniciar. Só se mesmo assim o serviço não arrancar, use `ollama serve` numa janela e deixe-a aberta. Nesse caso essa janela fica ocupada e a aplicação tem de ser iniciada noutra.

## Como executar

Com o Ollama já a correr em segundo plano, basta um terminal. Entre na pasta do projeto e inicie a interface:

```powershell
cd Meal_Planner
python notes_ui.py
```

`notes_ui.py` é o ficheiro de entrada. A janela do Meal Planner abre no ambiente de trabalho e fica aberta até a fechar. Para voltar a usar o programa noutro dia, confirme que o Ollama está ativo (ícone na área de notificação ou `ollama list`) e execute outra vez `python notes_ui.py`. O modelo `llama3.2` só precisa de ser descarregado uma vez. Sem o Ollama a correr, a geração de refeições não funciona.

## Ligação ao Spotify

A integração Spotify é opcional. Para utilizar as funcionalidades Spotify, é necessária uma configuração OAuth local. A experiência completa de reprodução requer uma conta **Spotify Premium**.

A aplicação usa OAuth através de `spotify.py`: abre a página de autorização do Spotify no navegador, inicia um servidor HTTP local em `127.0.0.1:8888`, recebe o callback em `/callback` e guarda o token de atualização em `spotify_token.json`.

1. **Confirme que tem Spotify Premium.** O controlo de reprodução (reproduzir, pausar, seguinte, etc.) e as funcionalidades de reprodução da Web API dependem do Premium. Numa conta gratuita, estas funções podem falhar ou ficar limitadas. A criação de playlists pode funcionar de forma mais abrangente, mas o Premium é necessário para a experiência Spotify completa.
2. Aceda a [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard), inicie sessão e selecione **Create app**.
3. Copie o **Client ID** e o **Client Secret** apresentados pela aplicação.
4. Abra **Edit settings**, adicione exatamente este **Redirect URI** e selecione **Save**:

   `http://127.0.0.1:8888/callback`

5. No mesmo PowerShell em que vai executar a aplicação, defina as variáveis de ambiente. Substitua os textos pelos valores copiados do dashboard:

   ```powershell
   $env:SPOTIFY_CLIENT_ID = "o_teu_client_id"
   $env:SPOTIFY_CLIENT_SECRET = "o_teu_client_secret"
   ```

   Estas variáveis só existem nessa janela do PowerShell. Se a fechar, defina-as outra vez antes de `python notes_ui.py`. O programa lê apenas estas variáveis; não abre um ficheiro `.env` sozinho. Pode guardar os valores num `.env` local como apontamento (o `.gitignore` ignora esse ficheiro), mas continua a ter de os passar à sessão com os comandos acima. Não faça commit do Client ID nem do Client Secret.

6. Nessa mesma sessão, entre na pasta do projeto e execute `python notes_ui.py`. Com a janela aberta, utilize uma funcionalidade Spotify (criar playlist ou controlar a reprodução). O navegador abre `accounts.spotify.com/authorize`; autorize o acesso com a conta Spotify. No final, o servidor local mostra **“Spotify connected. You can close this window.”** Pode fechar essa página do browser. A janela do Meal Planner mantém-se aberta.
7. O token fica em `spotify_token.json`, que é ignorado pelo Git. Nas vezes seguintes a aplicação usa esse token para renovar o acesso, mas continua a precisar de `SPOTIFY_CLIENT_ID` e `SPOTIFY_CLIENT_SECRET` na mesma sessão. Sem essas duas variáveis, a renovação falha mesmo que o ficheiro do token já exista. O Spotify também tem de ter um dispositivo ativo (a aplicação Spotify aberta no computador ou no telemóvel) para reproduzir, pausar ou mudar de faixa.
8. Se houver problemas:
   - Confirme que o Redirect URI no Dashboard é exatamente `http://127.0.0.1:8888/callback`, incluindo `127.0.0.1`, a porta `8888` e `/callback`.
   - Defina `SPOTIFY_CLIENT_ID` e `SPOTIFY_CLIENT_SECRET` na mesma sessão do PowerShell em que executa `python notes_ui.py`, também quando `spotify_token.json` já existe.
   - Se a porta `8888` estiver ocupada, encerre a outra cópia da aplicação ou o processo que a está a usar e tente outra vez.
   - Sem Spotify Premium, as funcionalidades de reprodução podem falhar ou ficar limitadas.

Os scopes pedidos pela aplicação são: `user-top-read`, `playlist-modify-private`, `ugc-image-upload`, `user-read-currently-playing`, `user-modify-playback-state` e `user-read-playback-state`.

## Estrutura principal

| Ficheiro | Papel |
|---|---|
| `notes_ui.py` | Interface Tkinter e ponto de entrada |
| `recipes.py` | Prompts e análise de receitas através do Ollama |
| `nutrition.py` | Consulta nutricional ao Open Food Facts |
| `meal_photos.py` | Pesquisa de fotografias dos pratos |
| `spotify.py` | Autenticação OAuth e integração com o Spotify |
| `playlists.py` | Criação de playlists e capas |
| `debuglog.py` | Registos locais de diagnóstico |
| `dave_grohl.png` / `oasis.png` | Imagens usadas nas capas Foo Fighters / Oasis |

Podem existir também na pasta os ficheiros opcionais `notes.c` e `notes.exe`.

## Dados locais e segredos

Estes ficheiros não devem ser versionados e são ignorados pelo `.gitignore`:

- `notes.txt` — dados da lista local
- `spotify_token.json` — token OAuth do Spotify
- `.env` — apontamento local de credenciais, se o criares; a aplicação não o lê automaticamente
- `__pycache__/`, `*.pyc` e `debug-*.log` — ficheiros gerados localmente

## Licença

Projeto pessoal de aprendizagem. As APIs do Ollama, Open Food Facts e Spotify têm os seus próprios termos de utilização.