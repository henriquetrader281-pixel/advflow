# AdvFlow — estúdio de conteúdo jurídico

Aplicação Streamlit para produção de conteúdo jurídico informativo, criação visual e monitoramento de notícias jurídicas.

## Corrigir perda de acesso e manter o login persistente

O login local em SQLite não é confiável em uma hospedagem cujo disco pode ser recriado. O AdvFlow agora pode guardar **apenas os registros de autenticação** em uma tabela persistente do Supabase; nome de usuário, hash e salt da senha, perfil, bloqueio por tentativas e estado da conta ficam no banco remoto.

### Configuração obrigatória (uma vez)

1. Crie ou abra seu projeto no [Supabase](https://supabase.com/dashboard).
2. No repositório, abra `supabase/schema.sql`, copie o conteúdo e execute no **SQL Editor** do seu projeto. Isso cria a tabela privada `public.advflow_users`.
3. No app hospedado pelo **Streamlit Community Cloud**, abra **Settings → Secrets** e configure:
   
   ```toml
   SUPABASE_URL = "https://SEU_PROJECT_REF.supabase.co"
   SUPABASE_SECRET_KEY = "SUA_CHAVE_SECRETA_DO_SUPABASE"
   ```
   
   Use a chave secreta do Supabase somente em Secrets, nunca no GitHub, no navegador ou em mensagens. O código também aceita `SUPABASE_SERVICE_ROLE_KEY` para projetos que ainda usam a chave legada.
4. Mantenha `INITIAL_SETUP_KEY` configurada, salve os Secrets e reinicie/republique o app.

Quando o banco remoto estiver vazio, a aplicação tenta migrar automaticamente os hashes dos usuários que ainda existirem no SQLite local. **A senha original não é migrada nem precisa ser conhecida pelo aplicativo**: só são transferidos o salt e o hash já armazenados. Se a hospedagem já apagou o banco local, use a chave privada `INITIAL_SETUP_KEY` na tela inicial para criar novamente o primeiro administrador; depois, esse cadastro ficará no Supabase.

Se o Supabase estiver configurado mas indisponível ou sem a tabela, o login falha de forma explícita em vez de criar silenciosamente uma conta em outro banco local.

**Limite importante:** esta mudança torna persistente o cadastro/login de usuários. Os rascunhos salvos ainda usam SQLite local nesta versão e podem exigir armazenamento persistente separado.

## Acesso e usuários

- Antes de abrir o app à internet, configure INITIAL_SETUP_KEY em Streamlit Secrets com um valor longo e aleatório privado. Essa chave protege a criação do primeiro administrador e não é a senha normal de login.
- No primeiro acesso, insira essa chave e crie o administrador na própria tela inicial. Use uma senha forte com pelo menos 12 caracteres.
- O administrador cria contas adicionais pela aba Usuários. Cada conta tem sua própria senha e vê apenas suas próprias peças salvas.
- Senhas temporárias são geradas pela aplicação e precisam ser alteradas no primeiro acesso. O administrador pode redefinir ou desativar contas.
- Cinco tentativas de senha incorreta bloqueiam o login por 15 minutos.
- Esta versão não envia recuperação de senha por e-mail; solicite redefinição ao administrador.
- Para não perder contas após uma nova implantação, configure o armazenamento de autenticação no Supabase seguindo a seção acima. Sem esses Secrets, o login permanece no SQLite local.

As senhas não são armazenadas em texto simples: a aplicação usa PBKDF2-HMAC-SHA256 com salt individual. A autenticação básica não substitui um sistema corporativo com MFA, auditoria centralizada, políticas de sessão e armazenamento persistente.

## Executar localmente (Windows PowerShell)

1. Crie um ambiente: py -m venv .venv
2. Ative-o: .venv\\Scripts\\Activate.ps1
3. Instale as dependências: pip install -r requirements.txt
4. Copie .streamlit/secrets.toml.example para .streamlit/secrets.toml e preencha suas chaves.
5. Inicie: streamlit run app.py

Não envie o arquivo secrets.toml real ao GitHub; ele é ignorado pelo .gitignore.

## Copy profissional e triagem ética da publicidade jurídica

Dentro da aba **Agente de conteúdo**, escolha **Copy profissional** no campo “Modo de criação” para produzir legendas, carrosséis, Reels, posts estáticos e artigos curtos com linguagem natural e sóbria, sem emojis por padrão, clickbait, urgência artificial, promessas de resultado ou chamadas de contratação. O modo reutiliza o tema, público, formato, área do Direito, tom e contexto do formulário principal. A peça resultante fica na mesma tela para edição, revisão linguística, revisão ética preliminar, salvamento e criação visual. O módulo usa a primeira integração disponível entre Gemini, Groq e OpenRouter.

Dentro da aba **Agente de conteúdo**, abra o expansor **Revisão OAB — triagem ética** para fazer uma triagem lexical preliminar de possíveis alertas na publicidade jurídica. Essa checagem ajuda a apontar expressões que merecem revisão, mas não certifica conformidade com as regras da OAB nem substitui a análise do profissional responsável.

Antes de publicar, confira o texto integral, o contexto e as regras oficiais vigentes. Use como referência o Provimento 205/2021 e o Código de Ética e Disciplina da OAB, sem tratar a triagem automática como parecer jurídico.

## Tipografia editorial e formatos de arte

No próprio **Agente de conteúdo**, a seção **Identidade visual e formato das artes** reúne paleta, composição, estilo, densidade visual, tipografia e proporção em um único lugar. O padrão selecionado agora é o par contemporâneo de títulos fortes e corpo limpo. Os cinco pares disponíveis são:
- **Clássico imponente:** Playfair Display + Montserrat.
- **Clássico editorial:** Cormorant Garamond + Inter.
- **Contemporâneo refinado:** Cinzel + Lato.
- **Moderno limpo:** Playfair Display + Open Sans.
- **Moderno premium (padrão):** Montserrat + Inter.

As fontes de código aberto são carregadas sob demanda e guardadas em cache; se o download falhar, o renderizador tenta fontes de sistema compatíveis. O AdvFlow aplica o texto com essas fontes depois que o fundo é gerado, para não depender das letras desenhadas pela IA. Isso melhora a nitidez e a fidelidade da copy, embora o fundo ainda deva ser conferido antes da publicação.

Formatos selecionáveis: **4:5 (1080 × 1350)**, **1:1 (1080 × 1080)** e **9:16 (1080 × 1920)**. Para reduzir a distorção, o prompt pede ao Gemini ou ao modelo de imagem um fundo de alta qualidade sem letras, números, logotipos ou pseudo-texto; depois, o AdvFlow sobrepõe o título e o corpo com tipografia real. Confira contraste, enquadramento e fidelidade do texto final antes de publicar.

## OpenRouter como provedor alternativo

O AdvFlow pode usar OpenRouter como terceiro provedor de texto, depois de Gemini e Groq e antes do rascunho local. O endpoint é compatível com a API de chat completions e o padrão sugerido é `openrouter/free`; modelos gratuitos têm disponibilidade e limites próprios.

1. Crie uma conta e gere uma API key em https://openrouter.ai/keys.
2. Abra o aplicativo no Streamlit Community Cloud → Settings → Secrets.
3. Adicione as duas linhas abaixo (com a sua chave real somente no painel Secrets):

```toml
OPENROUTER_API_KEY = "SUA_CHAVE_REAL_DO_OPENROUTER"
OPENROUTER_MODEL = "openrouter/free"
OPENROUTER_IMAGE_MODEL = "google/gemini-3.1-flash-image"
```

4. Salve e aguarde o aplicativo reiniciar.
5. Na aba **Configuração**, clique em **Testar conexão OpenRouter**.
6. Ao gerar copy, o AdvFlow tenta Gemini, depois Groq, depois OpenRouter e usa a estrutura local caso os três falhem.

Se desejar outro modelo, copie o identificador exato do catálogo oficial: https://openrouter.ai/models. O valor de `OPENROUTER_MODEL` deve ser o slug do modelo, por exemplo o identificador gratuito mostrado no catálogo, e não o nome de exibição.

**Segurança e custo:** nunca publique a chave no GitHub, não a envie por chat e configure limites de gasto/uso no painel OpenRouter se usar modelos pagos. O roteador `openrouter/free` direciona para modelos gratuitos compatíveis, mas capacidade, limite de requisições e disponibilidade podem variar. Para modelos pagos, a cobrança é separada das assinaturas do aplicativo Gemini ou de outros provedores.

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
- Criação visual dentro do Agente de conteúdo, com controles únicos de paleta, composição, estilo, densidade, tipografia e proporção; os mesmos ajustes são usados nos prompts e no renderizador.
- Fluxo Gemini Plus: copia um prompt para gerar fundo sem texto, importa a imagem gerada e aplica título e corpo em fonte real no AdvFlow; não exige chave de API para esse modo.
- Geração de imagens diretamente no AdvFlow pelo OpenRouter Image API, com catálogo dinâmico, modelo selecionável, imagem de referência opcional quando suportada e exibição do custo retornado pela API.
- Arte tipográfica vetorial local como alternativa sem geração de imagem por IA.
- Triagem lexical de publicidade jurídica.
- SQLite com separação de rascunhos por usuário.

## Integrações não implementadas

- Instagram/Meta: publicação automática, Insights e agendamento não estão conectados nesta versão.
- Dentro do Agente de conteúdo há três rotas visuais: usar Gemini Plus manualmente com fundo sem texto, gerar diretamente pelo OpenRouter Image API, ou criar uma arte tipográfica local.
- O modo automático de imagem no OpenRouter requer `OPENROUTER_API_KEY`, acesso a um modelo gerador de imagens e saldo/créditos. As solicitações de imagem não usam o roteador gratuito de texto `openrouter/free`; a cobrança depende do modelo, rota e parâmetros. Consulte os preços por modelo antes de gerar e comece com uma única imagem de teste.
- O modo manual Gemini Plus não automatiza nem reaproveita OAuth da CLI. O AdvFlow não usa tokens da sessão Gemini CLI para contornar o modelo de API. Para integração automática pela API Google, seria necessária uma chave de API Google com acesso ao modelo de imagem e sua própria política de cobrança.
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


## Fluxo de imagens com Gemini Plus

1. Na aba **Agente de conteúdo**, escolha o slide e configure a identidade visual uma única vez na seção **Identidade visual e formato das artes**.
2. Na seção de criação de arte, copie o **prompt do fundo — sem texto** e cole-o no aplicativo Gemini.
3. No Gemini, gere uma imagem sem letras, números, logotipos ou pseudo-texto e salve em PNG/JPG/WebP.
4. Volte ao mesmo Agente, envie o fundo e clique em **Aplicar tipografia e finalizar arte**. O AdvFlow aplica a paleta, a proporção escolhida, a tipografia e o texto exato do título/corpo.
5. Baixe a arte final em PNG e confira legibilidade, enquadramento e exatidão do texto antes de publicar.

A assinatura Google AI Plus amplia o acesso a recursos de imagem no aplicativo Gemini, mas não deve ser tratada como um saldo geral de chamadas de API em aplicações próprias. A API Gemini tem regras de limite e faturamento próprias. Consulte a [página oficial de planos Google AI](https://one.google.com/intl/pt-BR_br/about/google-ai-plans/) e a [documentação de faturamento da API Gemini](https://ai.google.dev/gemini-api/docs/billing?hl=pt).


## Geração direta de imagens com OpenRouter

Dentro do **Agente de conteúdo**, selecione **OpenRouter — geração direta pela API**. O sistema pode consultar o catálogo de imagens em tempo real em `GET /api/v1/images/models`, gerar por `POST /api/v1/images`, decodificar o resultado e aplicar a identidade visual do escritório.

- A chamada de geração só acontece depois de marcar a confirmação de custo e clicar no botão de geração.
- O modelo e seus parâmetros são escolhidos do catálogo quando este é carregado; os parâmetros opcionais só são enviados se forem anunciados como suportados pelo modelo. A variável `OPENROUTER_IMAGE_MODEL` escolhe o padrão; deixe o valor indicado acima ou use o identificador exato de outro modelo do catálogo.
- Uma imagem de referência é opcional e somente é enviada se o modelo indicar suporte a `input_references`.
- O custo é exibido quando a API o inclui na resposta. Ausência desse dado não significa geração gratuita.
- O catálogo e os preços podem mudar. Confira o custo por modelo antes de produzir muitas imagens e comece com uma única arte de teste.

Documentação oficial: [Image Generation API](https://openrouter.ai/blog/tutorials/image-generation/) · [Modelos de imagem](https://openrouter.ai/collections/image-models/).

