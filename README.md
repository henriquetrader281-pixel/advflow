# AdvFlow — estúdio de conteúdo jurídico

Aplicação Streamlit para produção de conteúdo informativo, arte tipográfica, métricas manuais, calendário editorial e monitoramento de notícias jurídicas.

## Acesso e usuários

- No primeiro acesso, crie o administrador na própria tela inicial. Use uma senha forte com pelo menos 12 caracteres.
- O administrador cria contas adicionais pela aba Usuários. Cada conta tem sua própria senha e vê apenas suas peças, métricas e calendário.
- Senhas temporárias são geradas pela aplicação e precisam ser alteradas no primeiro acesso. O administrador pode redefinir ou desativar contas.
- Cinco tentativas de senha incorreta bloqueiam o login por 15 minutos.
- Esta versão não envia recuperação de senha por e-mail; solicite redefinição ao administrador.

As senhas não são armazenadas em texto simples: a aplicação usa PBKDF2-HMAC-SHA256 com salt individual. A autenticação básica não substitui um sistema corporativo com MFA, auditoria centralizada, políticas de sessão e armazenamento persistente.

## Executar localmente (Windows PowerShell)

1. Crie um ambiente: py -m venv .venv
2. Ative-o: .venv\\Scripts\\Activate.ps1
3. Instale as dependências: pip install -r requirements.txt
4. Copie .streamlit/secrets.toml.example para .streamlit/secrets.toml e preencha suas chaves.
5. Inicie: streamlit run app.py

Não envie o arquivo secrets.toml real ao GitHub; ele é ignorado pelo .gitignore.

## Modelos de IA

- Gemini: crie uma chave em https://aistudio.google.com/apikey. Modelo padrão sugerido: gemini-3.8-flash.
- Groq: crie uma chave em https://console.groq.com/keys. Modelo padrão sugerido: openai/gpt-oss-120b.
- A disponibilidade e os limites dependem da conta. Confira as listas oficiais: https://ai.google.dev/gemini-api/docs/models e https://console.groq.com/docs/models.

## Publicar no Streamlit Community Cloud

1. Acesse https://share.streamlit.io e entre com GitHub.
2. Crie um app apontando para henriquetrader281-pixel/advflow, branch main, arquivo app.py.
3. Em Settings → Secrets, configure as chaves reais e os modelos escolhidos.
4. Faça deploy ou reinicie o app. Se já existirem secrets com modelos antigos, atualize-os no painel.

## Radar jurídico

O radar reúne fontes predefinidas: STJ, CNJ, TSE, Agência Câmara por tema e Migalhas. Filtra por área do Direito, período e termo; estima relevância por atualidade, tipo de fonte e correspondência temática. É possível informar feeds RSS adicionais e encaminhar uma notícia como contexto para o agente.

- STJ: https://res.stj.jus.br/hrestp-c-portalp/RSS.xml
- CNJ: https://www.cnj.jus.br/?format=feed&type=rss
- TSE: https://www.tse.jus.br/rss
- Câmara — Direito e Justiça: https://www.camara.leg.br/noticias/rss/dinamico/DIREITO-E-JUSTICA
- Migalhas: https://www.migalhas.com.br/rss/rss.xml

RSS pode ficar indisponível ou omitir datas/resumos. A classificação temática é heurística e não valida o conteúdo jurídico; confira a notícia e o documento primário antes de publicar.

## Funcionalidades

- Agente de conteúdo com Gemini, Groq alternativo e estrutura local em caso de indisponibilidade.
- Geração de imagens PNG tipográficas para posts.
- Métricas manuais e exportação CSV.
- Calendário editorial e exportação CSV.
- Triagem lexical de publicidade jurídica.
- SQLite com separação de dados por usuário.

## Integrações não implementadas

- Instagram Insights, publicação e agendamento automáticos dependem de integração própria com a API oficial da Meta, permissões e tokens.
- O estúdio atual cria artes tipográficas; não gera ilustrações/fotografias por IA.
- Recuperação de senha por e-mail, MFA e banco de dados gerenciado não estão implementados.

## Avisos operacionais

- O banco SQLite pode perder persistência em hospedagem cujo disco é temporário. Configure armazenamento persistente e backups antes de uso em equipe.
- Não armazene dados confidenciais de clientes neste protótipo.
- O verificador OAB é uma triagem automatizada; não garante conformidade. O advogado responsável deve revisar texto, contexto, regras atuais e fontes.
- Resumos e textos de IA podem conter erros. Confirme legislação, prazos, julgados e teses em fontes oficiais.

## Estrutura

- app.py: aplicação principal
- requirements.txt: dependências
- .streamlit/secrets.toml.example: exemplo sem credenciais reais
- static/index.html: landing page estática
- .github/workflows/validate.yml: verificação de sintaxe
