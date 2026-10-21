# AdvFlow — estúdio de conteúdo jurídico

Aplicação Streamlit para produção de conteúdo informativo, arte tipográfica, métricas manuais, calendário editorial e monitoramento de notícias jurídicas.

## Acesso e usuários

- Antes de abrir o app à internet, configure INITIAL_SETUP_KEY em Streamlit Secrets com um valor longo e aleatório privado. Essa chave protege a criação do primeiro administrador e não é a senha normal de login.
- No primeiro acesso, insira essa chave e crie o administrador na própria tela inicial. Use uma senha forte com pelo menos 12 caracteres.
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
- Estúdio visual integrado ao fluxo manual do Google AI Plus/Gemini: cria prompts de direção de arte específicos por slide, permite importar a imagem gerada no app Gemini e aplica copy, paleta e assinatura num PNG 4:5.
- Arte tipográfica vetorial local como alternativa sem imagem IA.
- Este fluxo de importação usa a assinatura do aplicativo Gemini manualmente e não exige chave de API Gemini.
- Validador de métricas por usuário: metas personalizadas, limites de ação, comparação de janelas e recortes por etapa do funil e formato.
- Indicadores de alcance, interações por alcance, salvamentos por alcance, compartilhamentos por alcance, visitas ao perfil por alcance e cliques por visita.
- Alertas de qualidade: datas inválidas, alcance zero, temas vazios, dados parciais e possíveis registros duplicados; os dados antigos não são alterados automaticamente.
- Configuração de metas por histórico e objetivo editorial, sem aplicar benchmarks universais ou apresentar correlação como causalidade.
- Métricas manuais e exportação CSV.
- Calendário editorial e exportação CSV.
- Triagem lexical de publicidade jurídica.
- SQLite com separação de dados por usuário.

## Integrações não implementadas

- Instagram Insights, publicação e agendamento automáticos dependem de integração própria com a API oficial da Meta, permissões e tokens. O validador atual usa dados inseridos manualmente; a coluna de cobertura serve para marcar se os campos de Insights estão completos ou parciais.
- O estúdio oferece duas opções: importar imagens criadas manualmente no aplicativo Gemini (com a assinatura do usuário) e finalizar no AdvFlow; ou criar uma arte tipográfica local. A importação não aciona a API de geração de imagens.
- Para gerar imagens automaticamente dentro da aplicação via API Gemini, é necessário configurar uma chave de API em Streamlit Secrets. A API tem limites e faturamento próprios, separados da assinatura do app Gemini; confira as cotas e preços oficiais antes de ativar esse fluxo.
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


## Validação das métricas

O validador aplica uma abordagem de gestão por indicadores e níveis de ação: escolha indicadores que correspondam ao objetivo editorial, defina uma meta e um limite que pede revisão e compare períodos equivalentes. Não existem metas predefinidas universais no sistema, pois taxas variam por perfil, formato, público, período e objetivo.

- **Distribuição:** alcance médio por publicação.
- **Interação:** interações agregadas divididas pelo alcance total.
- **Utilidade:** salvamentos e compartilhamentos divididos pelo alcance.
- **Jornada:** visitas ao perfil divididas pelo alcance e cliques em links divididos pelas visitas registradas, tratados como indicadores-proxy quando os dados têm janelas e fontes comparáveis.
- **Qualidade de dados:** amostra pequena, dados parciais, alcance zero, datas inválidas, tema vazio e possíveis duplicatas são sinalizados.
- **Limite de interpretação:** o painel descreve associações e variações; não prova que um post causou uma mudança. Para testar decisões, mantenha comparáveis a janela, o público, o formato e o objetivo e registre o contexto de cada alteração.

No conteúdo de advocacia, métricas de desempenho não autorizam promessas de resultados, comparação profissional, exposição de casos ou chamadas que induzam contratação. A revisão ética continua sendo responsabilidade do profissional. Consulte o [Provimento 205/2021 da OAB](https://www.oab.org.br/leisnormas/legislacao/provimentos/205-2021) e as orientações oficiais vigentes.


## Fluxo de imagens com Gemini Plus

1. Na aba **Estúdio de imagens**, selecione o slide e o estilo visual.
2. Baixe o prompt gerado pelo AdvFlow ou copie-o para o aplicativo Gemini.
3. No Gemini, gere uma imagem sem textos e salve o resultado em PNG/JPG/WebP.
4. Volte ao AdvFlow, envie a imagem e clique em **Aplicar identidade visual e copy**. O app faz o corte vertical 4:5 e aplica a paleta, a tipografia, o título, o corpo e a assinatura do escritório.
5. Baixe a arte final em PNG e confira legibilidade, enquadramento e exatidão do texto antes de publicar.

A assinatura Google AI Plus amplia o acesso a recursos de imagem no aplicativo Gemini, mas não deve ser tratada como um saldo geral de chamadas de API em aplicações próprias. A API Gemini tem regras de limite e faturamento próprias. Consulte a [página oficial de planos Google AI](https://one.google.com/intl/pt-BR_br/about/google-ai-plans/) e a [documentação de faturamento da API Gemini](https://ai.google.dev/gemini-api/docs/billing?hl=pt).
