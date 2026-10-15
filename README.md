# AdvFlow — marketing jurídico responsável

Aplicação Streamlit + Python para apoiar a criação de conteúdo jurídico informativo, artes para redes sociais, registro manual de métricas, radar RSS, calendário editorial e triagem lexical de publicidade.

## Executar localmente (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .streamlit/secrets.toml.example .streamlit/secrets.toml
notepad .streamlit/secrets.toml
streamlit run app.py
```

Edite `secrets.toml` e insira suas chaves privadas. Nunca envie o arquivo real de secrets ao GitHub.

## APIs e credenciais
- Gemini: obtenha chave em https://aistudio.google.com/apikey e configure `GEMINI_API_KEY`.
- Groq: obtenha chave em https://console.groq.com/keys e configure `GROQ_API_KEY` como alternativa.
- Modelos, limites e disponibilidade podem mudar; confirme nos painéis oficiais.

## Publicar no Streamlit Community Cloud
1. Acesse https://share.streamlit.io e entre com GitHub.
2. Clique para criar um app e selecione `henriquetrader281-pixel/advflow`, branch `main`, arquivo `app.py`.
3. Em configurações do app, adicione os valores TOML de secrets, incluindo as chaves reais.
4. Faça deploy. O deploy precisa ser acionado e as chaves precisam ser configuradas por você no painel.

## Funcionalidades
- Agente de conteúdo com Gemini, fallback Groq e estrutura local de contingência.
- Criação de carrosséis/capas PNG tipográficas com logo opcional.
- Cadastro manual e comparação básica de métricas.
- Leitura de feeds RSS jurídicos.
- Calendário editorial e exportação CSV.
- Triagem de expressões potencialmente problemáticas em publicidade.
- Persistência local SQLite.

## Limites importantes
- Instagram Insights e publicação/agendamento automático não estão conectados. Isso exige API oficial da Meta, conta profissional elegível, app, tokens, permissões e possivelmente revisão da Meta.
- O gerador de artes cria composições tipográficas, não ilustrações/fotografias por IA.
- A triagem OAB é lexical e não certifica conformidade. O advogado responsável deve conferir regras vigentes, contexto, fontes e peça final.
- Notícias RSS são pistas editoriais, não fontes jurídicas definitivas. Confirme em legislação, tribunais e diários oficiais.
- Não há login multiusuário nem criptografia do SQLite por padrão. Não inserir dados confidenciais de clientes.
- Modelos de IA podem inventar informações. Verifique cada lei, artigo, prazo e decisão em fonte oficial antes de publicar.

## Estrutura
`app.py`, `requirements.txt`, `.streamlit/secrets.toml.example`, `.gitignore`, `static/index.html` e este README.
