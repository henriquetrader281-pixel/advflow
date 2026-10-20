import os, re, json, sqlite3, io, hashlib, secrets, html, unicodedata
from datetime import datetime, date, timedelta
import requests, feedparser, pandas as pd, streamlit as st
from PIL import Image, ImageDraw, ImageFont

st.set_page_config(page_title="AdvFlow | Marketing jurídico", page_icon="⚖️", layout="wide")
DB = os.getenv("ADVFLOW_DB", "advflow.db")

def secret(k, default=""):
    try: return str(st.secrets.get(k, default))
    except Exception: return os.getenv(k, default)

def db():
    c=sqlite3.connect(DB, check_same_thread=False); c.row_factory=sqlite3.Row; return c
with db() as c:
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_salt TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        active INTEGER NOT NULL DEFAULT 1,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT,
        created_at TEXT NOT NULL
    )""")
    c.execute("CREATE TABLE IF NOT EXISTS pieces(id INTEGER PRIMARY KEY, created TEXT, subject TEXT, format TEXT, content TEXT, status TEXT, user_id TEXT NOT NULL DEFAULT 'legacy')")
    c.execute("CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY, post_date TEXT, topic TEXT, format TEXT, reach INTEGER, likes INTEGER, comments INTEGER, saves INTEGER, shares INTEGER, user_id TEXT NOT NULL DEFAULT 'legacy')")
    c.execute("CREATE TABLE IF NOT EXISTS calendar(id INTEGER PRIMARY KEY, pub_date TEXT, pub_time TEXT, title TEXT, format TEXT, status TEXT, notes TEXT, user_id TEXT NOT NULL DEFAULT 'legacy')")
    migrations = {
        "users": {"role":"TEXT NOT NULL DEFAULT 'user'","active":"INTEGER NOT NULL DEFAULT 1","must_change_password":"INTEGER NOT NULL DEFAULT 0","failed_attempts":"INTEGER NOT NULL DEFAULT 0","locked_until":"TEXT","created_at":"TEXT"},
        "pieces": {"user_id":"TEXT NOT NULL DEFAULT 'legacy'"},
        "metrics": {"user_id":"TEXT NOT NULL DEFAULT 'legacy'"},
        "calendar": {"user_id":"TEXT NOT NULL DEFAULT 'legacy'"}
    }
    for table_name, cols in migrations.items():
        existing = {row[1] for row in c.execute(f"PRAGMA table_info({table_name})").fetchall()}
        for col_name, col_type in cols.items():
            if col_name not in existing:
                c.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}")

def password_record(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 310000).hex()
    return salt, digest

def password_matches(password, salt, saved_hash):
    candidate = password_record(password, salt)[1]
    return secrets.compare_digest(candidate, saved_hash)

def valid_username(username):
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username or ""))

def auth_gate():
    st.title("AdvFlow | Acesso seguro")
    st.caption("Contas individuais e senhas protegidas por hash.")
    with db() as c:
        account_count = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    username = st.session_state.get("auth_user")
    account = None
    if username:
        with db() as c:
            account = c.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if account is None:
            st.session_state.pop("auth_user", None)
            username = None
    if account_count == 0:
        st.info("Primeiro acesso: configure uma chave de instalação privada em Streamlit Secrets e use-a somente para criar o primeiro administrador.")
        setup_key_expected=secret("INITIAL_SETUP_KEY")
        if not setup_key_expected:
            st.error("Configuração pendente: adicione INITIAL_SETUP_KEY em Settings → Secrets antes de liberar o cadastro inicial. Gere uma chave longa e aleatória e não use o valor de exemplo.")
            st.stop()
        with st.form("initial_admin_setup"):
            setup_key = st.text_input("Chave de instalação", type="password")
            new_user = st.text_input("Usuário administrador", placeholder="ex.: henrique.souza")
            pw1 = st.text_input("Crie uma senha", type="password")
            pw2 = st.text_input("Confirme a senha", type="password")
            submitted = st.form_submit_button("Criar administrador", type="primary")
        if submitted:
            clean_user = (new_user or "").strip().lower()
            if not secrets.compare_digest(setup_key, setup_key_expected):
                st.error("Chave de instalação inválida.")
            elif not valid_username(clean_user):
                st.error("O usuário deve ter de 3 a 32 caracteres: letras, números, ponto, hífen ou sublinhado.")
            elif len(pw1) < 12:
                st.error("Use uma senha com pelo menos 12 caracteres.")
            elif pw1 != pw2:
                st.error("As senhas não conferem.")
            else:
                salt, digest = password_record(pw1)
                with db() as c:
                    c.execute("INSERT INTO users(username,password_salt,password_hash,role,active,created_at) VALUES(?,?,?,?,?,?)",
                              (clean_user,salt,digest,"admin",1,datetime.now().isoformat(timespec="seconds")))
                    for table_name in ("pieces","metrics","calendar"):
                        c.execute(f"UPDATE {table_name} SET user_id=? WHERE user_id='legacy'",(clean_user,))
                st.session_state["auth_user"] = clean_user
                st.rerun()
        st.stop()
    if account is None:
        with st.form("login_form"):
            login_user = st.text_input("Usuário")
            login_password = st.text_input("Senha", type="password")
            login_submit = st.form_submit_button("Entrar", type="primary")
        if login_submit:
            clean_user = (login_user or "").strip().lower()
            with db() as c:
                record = c.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (clean_user,)).fetchone()
                now = datetime.now()
                if record and record["active"]:
                    locked_until = record["locked_until"]
                    if locked_until and datetime.fromisoformat(locked_until) > now:
                        st.error("Conta temporariamente bloqueada após falhas de autenticação. Tente mais tarde ou solicite redefinição ao administrador.")
                        st.stop()
                    if password_matches(login_password,record["password_salt"],record["password_hash"]):
                        c.execute("UPDATE users SET failed_attempts=0,locked_until=NULL WHERE username=?",(clean_user,))
                        st.session_state["auth_user"] = record["username"]
                        st.rerun()
                    failures = int(record["failed_attempts"] or 0) + 1
                    lock_time = (now + timedelta(minutes=15)).isoformat(timespec="seconds") if failures >= 5 else None
                    c.execute("UPDATE users SET failed_attempts=?,locked_until=? WHERE username=?",(failures,lock_time,clean_user))
            st.error("Usuário ou senha inválidos. Cinco tentativas incorretas bloqueiam o acesso por 15 minutos.")
        st.caption("Esqueceu a senha? Solicite ao administrador uma redefinição. Esta versão não envia e-mails de recuperação.")
        st.stop()
    if account["must_change_password"]:
        st.warning("Por segurança, altere a senha temporária para continuar.")
        with st.form("required_password_change"):
            pw1 = st.text_input("Nova senha",type="password")
            pw2 = st.text_input("Confirme a nova senha",type="password")
            submit_change = st.form_submit_button("Salvar nova senha",type="primary")
        if submit_change:
            if len(pw1) < 12: st.error("A senha deve ter pelo menos 12 caracteres.")
            elif pw1 != pw2: st.error("As senhas não conferem.")
            else:
                salt,digest=password_record(pw1)
                with db() as c:
                    c.execute("UPDATE users SET password_salt=?,password_hash=?,must_change_password=0,failed_attempts=0,locked_until=NULL WHERE username=?",
                              (salt,digest,account["username"]))
                st.success("Senha atualizada.")
                st.rerun()
        st.stop()
    return dict(account)

current_user = auth_gate()
user_id = current_user["username"]

SYSTEM = """Você é estrategista de conteúdo jurídico brasileiro. Escreva em português claro, informativo e sóbrio; sem sensacionalismo, promessa de resultado, comparação profissional, captação indevida ou invenção de leis/julgados. Inclua fontes para conferência e aviso de conteúdo informativo. Retorne JSON com titulo, resumo_estrategico, slides (lista de titulo/texto/visual), roteiro (lista de cena/fala/visual), legenda, hashtags e fontes_para_conferir. Carrossel: 6-8 slides; Reel: 4 cenas; Post estático: 1 slide; Legenda: sem slides."""
PATTERNS=[
(r"garantimos|garantia de resultado|causa ganha|resultado garantido","Possível promessa de resultado."),
(r"melhor advogado|número\\s*1|líder absoluto|maior escritório","Possível superlativo ou comparação."),
(r"promoção|desconto|grátis|preço da consulta|R\\$\\s*\\d+","Possível apelo promocional ou preço."),
(r"contrate agora|ligue agora|chame no whatsapp|últimas vagas|clique e contrate","Possível captação comercial direta."),
(r"ganhe dinheiro|indenização certa|causa certa","Possível promessa de ganho."),
(r"processo nº\\s*\\d+|caso real do cliente","Verifique sigilo e dados identificáveis."),
(r"urgente|chocante|imperdível|absurdo","Verifique linguagem sensacionalista.")
]
def review(text):
    return [msg for pattern,msg in PATTERNS if re.search(pattern,text or "",re.I)] or ["Nenhum termo de alerta foi encontrado pela triagem automática. Isso não garante conformidade com as regras da OAB."]

def local_draft(subject, fmt, audience, goal):
    titles=["Entenda o tema","O conceito","O que observar","Cuidados importantes","Na prática","Em resumo"]
    slides=[{"titulo":t,"texto":f"Explique {subject} com linguagem acessível, sem generalizar situações individuais. Confirme regras e prazos em fonte oficial.","visual":"Composição tipográfica institucional"} for t in titles]
    if fmt=="Post estático": slides=slides[:1]
    if fmt=="Legenda": slides=[]
    return {"titulo":subject,"resumo_estrategico":f"Conteúdo para {audience}; objetivo: {goal}. Estrutura-base local para edição e checagem.","slides":slides,
    "roteiro":[{"cena":"Abertura","fala":f"Você conhece os principais pontos sobre {subject}?","visual":"Apresentador com título na tela"},{"cena":"Explicação","fala":"Vamos organizar as informações de forma clara. A aplicação depende dos fatos e das normas vigentes.","visual":"Palavras-chave"},{"cena":"Cuidados","fala":"Confira documentos, prazos e fontes oficiais antes de tomar decisões.","visual":"Lista curta"},{"cena":"Fechamento","fala":"Conteúdo informativo; não substitui análise individual.","visual":"Assinatura institucional"}] if fmt=="Reel" else [],
    "legenda":f"{subject}: informação jurídica geral. Cada situação deve ser analisada conforme seus fatos e as regras aplicáveis. Este conteúdo não substitui orientação individual.",
    "hashtags":["#Direito","#InformaçãoJurídica"],"fontes_para_conferir":["Legislação oficial aplicável","Portal oficial do tribunal ou órgão competente"]}
def call_gemini(prompt):
    key=secret("GEMINI_API_KEY")
    if not key: raise RuntimeError("GEMINI_API_KEY não configurada")
    model=secret("GEMINI_MODEL","gemini-3.8-flash")
    r=requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",params={"key":key},
      json={"systemInstruction":{"parts":[{"text":SYSTEM}]},"contents":[{"role":"user","parts":[{"text":prompt}]}],"generationConfig":{"temperature":0.55,"responseMimeType":"application/json"}},timeout=60)
    if not r.ok: raise RuntimeError(f"Gemini HTTP {r.status_code}")
    return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
def call_groq(prompt):
    key=secret("GROQ_API_KEY")
    if not key: raise RuntimeError("GROQ_API_KEY não configurada")
    model=secret("GROQ_MODEL","openai/gpt-oss-120b")
    r=requests.post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":"Bearer "+key},
      json={"model":model,"temperature":0.55,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":prompt}]},timeout=60)
    if not r.ok: raise RuntimeError(f"Groq HTTP {r.status_code}")
    return json.loads(r.json()["choices"][0]["message"]["content"])
def generate(subject, fmt, audience, goal, details=""):
    brief=json.dumps({"assunto":subject,"formato":fmt,"publico":audience,"objetivo":goal,"contexto_fontes_e_restricoes":details},ensure_ascii=False)
    errors=[]
    for name,fn in [("Gemini",call_gemini),("Groq (alternativo)",call_groq)]:
        try: return fn("Crie a peça a partir deste briefing. Não invente referências jurídicas. "+brief),name
        except Exception as e: errors.append(name+": "+str(e))
    result=local_draft(subject,fmt,audience,goal)
    result["aviso"]="IA externa indisponível; estrutura local usada. "+" | ".join(errors)
    return result,"Modelo local"

PALETTES={"Azul-marinho e dourado":("#F6F3EC","#172A46","#B69A5A"),"Verde institucional":("#F2F6F2","#1A4038","#6E9B88"),"Vinho e off-white":("#F8F2F0","#451D2C","#B17D89"),"Grafite minimalista":("#F4F5F6","#242A31","#728394")}
def make_art(title, body, palette, signature=""):
    bg,ink,accent=PALETTES[palette]; im=Image.new("RGB",(1080,1350),bg); d=ImageDraw.Draw(im)
    def font(sz,bold=False):
        for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","C:/Windows/Fonts/arial.ttf"):
            try:return ImageFont.truetype(p,sz)
            except:pass
        return ImageFont.load_default()
    d.rectangle((0,0,1080,22),fill=accent); d.rectangle((72,92,150,101),fill=accent)
    def wrap(txt,y,f,maxw,color,leading):
        words=str(txt).split(); lines=[]; line=""
        for w in words:
            trial=(line+" "+w).strip()
            if d.textbbox((0,0),trial,font=f)[2] < maxw or not line: line=trial
            else: lines.append(line); line=w
        if line:lines.append(line)
        for ln in lines:d.text((72,y),ln,font=f,fill=color); y+=leading
    wrap(title,170,font(66,True),920,ink,82)
    d.rounded_rectangle((72,390,1008,395),radius=2,fill=accent)
    wrap(body,440,font(35),900,ink,52)
    d.text((72,1240),signature or "CONTEÚDO INFORMATIVO",font=font(22,True),fill=accent)
    d.text((72,1280),"Confira as fontes. Cada caso exige análise individual.",font=font(18),fill=ink)
    out=io.BytesIO(); im.save(out,format="PNG"); out.seek(0); return out

st.title("⚖️ AdvFlow")
st.caption("Estúdio de conteúdo jurídico informativo • agente de IA • calendário • métricas • radar jurídico")
with st.sidebar:
    st.markdown(f"**Conta:** {current_user['username']} · {current_user['role']}")
    if st.button("Sair da conta", key="logout_button"):
        st.session_state.pop("auth_user", None)
        st.rerun()
    st.divider()
    st.subheader("Identidade do escritório")
    office=st.text_input("Nome / assinatura",value=secret("OFFICE_NAME","Seu escritório"))
    oab=st.text_input("Identificação OAB (opcional)",value=secret("OFFICE_OAB",""))
    palette=st.selectbox("Paleta visual",list(PALETTES))
    st.divider()
    st.caption("Use IA como apoio editorial. Revise fontes e conformidade antes de publicar.")
tab_labels=["Painel","Agente de conteúdo","Estúdio de imagens","Métricas","Radar jurídico","Calendário","Revisão OAB","Configuração"]
if current_user["role"] == "admin":
    tab_labels.append("Usuários")
tabs=st.tabs(tab_labels)

with tabs[0]:
    st.subheader("Painel")
    with db() as c:
        a=c.execute("SELECT COUNT(*) n FROM pieces WHERE user_id=?",(user_id,)).fetchone()["n"]
        b=c.execute("SELECT COUNT(*) n FROM metrics WHERE user_id=?",(user_id,)).fetchone()["n"]
        cal=c.execute("SELECT COUNT(*) n FROM calendar WHERE user_id=? AND status!='Publicado'",(user_id,)).fetchone()["n"]
    x,y,z=st.columns(3); x.metric("Peças salvas",a); y.metric("Registros de métricas",b); z.metric("Itens no calendário",cal)
    st.info("Comece pelo Agente de conteúdo. O AdvFlow salva peças, métricas e calendário no SQLite.")

with tabs[1]:
    st.subheader("Agente de conteúdo")
    brief_version=int(st.session_state.get("brief_version",0))
    subject_key=f"brief_subject_{brief_version}"
    details_key=f"brief_details_{brief_version}"
    with st.form("brief"):
        subject=st.text_input("Tema jurídico",value=st.session_state.get("news_brief_subject",""),key=subject_key,placeholder="Ex.: direitos do consumidor em compras online")
        fmt=st.selectbox("Formato",["Carrossel","Reel","Post estático","Legenda"])
        audience=st.text_input("Público-alvo",placeholder="Ex.: consumidores, pequenos empresários")
        goal=st.selectbox("Objetivo editorial",["Educar","Explicar mudança normativa","Responder dúvida frequente","Desmistificar um conceito","Orientar sobre prevenção"])
        details=st.text_area("Contexto, fonte ou pontos obrigatórios (opcional)",value=st.session_state.get("news_brief_details",""),key=details_key,height=125)
        submit=st.form_submit_button("Gerar conteúdo",type="primary")
    if submit:
        if not subject.strip(): st.warning("Informe o tema.")
        else:
            with st.spinner("Preparando rascunho…"): data,provider=generate(subject,fmt,audience or "público geral",goal,details)
            if details:
                data["contexto_informado"]=details
                if "URL original:" in details:
                    source_url=next((line.split(":",1)[1].strip() for line in details.splitlines() if line.startswith("URL original:")), "")
                    if source_url: data["fontes_para_conferir"]=list(data.get("fontes_para_conferir",[]))+["Notícia original para revisão: "+source_url]
            st.session_state.pop("news_brief_subject",None)
            st.session_state.pop("news_brief_details",None)
            st.session_state.pop("news_brief_ready",None)
            st.session_state["draft"]=data; st.session_state["draft_provider"]=provider
    if "draft" in st.session_state:
        d=st.session_state["draft"]; st.caption("Motor utilizado: "+st.session_state.get("draft_provider",""))
        if d.get("aviso"):st.warning(d["aviso"])
        st.write("**Título:**",d.get("titulo","")); st.write(d.get("resumo_estrategico",""))
        for i,s in enumerate(d.get("slides",[]),1):
            with st.expander(f"{i}. {s.get('titulo','Slide')}"):
                st.write(s.get("texto","")); st.caption(s.get("visual",""))
        if d.get("roteiro"):st.dataframe(pd.DataFrame(d["roteiro"]),use_container_width=True,hide_index=True)
        st.text_area("Legenda editável",d.get("legenda",""),key="draft_caption",height=130)
        st.write("**Hashtags:**"," ".join(d.get("hashtags",[])))
        st.write("**Fontes para conferir:**")
        for s in d.get("fontes_para_conferir",[]):st.write("- "+str(s))
        if st.button("Salvar rascunho"):
            with db() as c:c.execute("INSERT INTO pieces(created,subject,format,content,status,user_id) VALUES(?,?,?,?,?,?)",(datetime.now().isoformat(timespec="seconds"),d.get("titulo",subject),fmt,json.dumps(d,ensure_ascii=False),"Rascunho",user_id))
            st.success("Rascunho salvo.")
        st.download_button("Baixar conteúdo JSON",json.dumps(d,ensure_ascii=False,indent=2),"advflow_conteudo.json","application/json")

with tabs[2]:
    st.subheader("Estúdio de imagens")
    st.write("Cria artes tipográficas PNG para capa ou slide. Não gera fotografia ou ilustração por IA.")
    draft=st.session_state.get("draft",{})
    title=st.text_input("Título da arte",value=draft.get("titulo","Tema jurídico"))
    body=st.text_area("Texto da arte",value=(draft.get("slides") or [{"texto":"Informação jurídica geral."}])[0].get("texto","Informação jurídica geral."),height=110)
    if st.button("Gerar arte PNG"):
        buf=make_art(title,body,palette,office+((" • "+oab) if oab else ""))
        st.image(buf,use_container_width=True); st.download_button("Baixar arte PNG",buf.getvalue(),"advflow_arte.png","image/png")

with tabs[3]:
    st.subheader("Métricas manuais")
    with st.form("metric_form"):
        mdate=st.date_input("Data da publicação",value=date.today())
        topic=st.text_input("Tema / título")
        mformat=st.selectbox("Formato",["Carrossel","Reel","Post estático","Story","Outro"])
        cols=st.columns(5); reach=cols[0].number_input("Alcance",min_value=0,step=1); likes=cols[1].number_input("Curtidas",min_value=0,step=1); comments=cols[2].number_input("Comentários",min_value=0,step=1); saves=cols[3].number_input("Salvamentos",min_value=0,step=1); shares=cols[4].number_input("Compartilhamentos",min_value=0,step=1)
        save_m=st.form_submit_button("Salvar métricas")
    if save_m:
        with db() as c:c.execute("INSERT INTO metrics(post_date,topic,format,reach,likes,comments,saves,shares,user_id) VALUES(?,?,?,?,?,?,?,?,?)",(str(mdate),topic,mformat,reach,likes,comments,saves,shares,user_id))
        st.success("Métricas registradas.")
    with db() as c: rows=[dict(r) for r in c.execute("SELECT * FROM metrics WHERE user_id=? ORDER BY post_date DESC",(user_id,))]
    if rows:
        df=pd.DataFrame(rows); df["engajamentos"]=df[["likes","comments","saves","shares"]].sum(axis=1)
        df["engajamento/alcance %"]=(df["engajamentos"]/df["reach"].replace(0,pd.NA)*100).round(2)
        st.dataframe(df,use_container_width=True,hide_index=True)
        st.download_button("Exportar CSV",df.to_csv(index=False).encode("utf-8-sig"),"advflow_metricas.csv","text/csv")
        st.bar_chart(df.groupby("format")[["likes","comments","saves","shares"]].sum())

with tabs[4]:
    st.subheader("Radar jurídico | notícias com utilidade editorial")
    st.caption("Fontes oficiais e portais jurídicos, com filtros temáticos, data e relevância estimada. A classificação automática serve para organizar pautas, não para validar teses jurídicas.")
    NEWS_SOURCES = {
        "STJ — notícias oficiais": ("https://res.stj.jus.br/hrestp-c-portalp/RSS.xml","Oficial"),
        "CNJ — notícias oficiais": ("https://www.cnj.jus.br/?format=feed&type=rss","Oficial"),
        "TSE — Justiça Eleitoral": ("https://www.tse.jus.br/rss","Oficial"),
        "Câmara — Direito e Justiça": ("https://www.camara.leg.br/noticias/rss/dinamico/DIREITO-E-JUSTICA","Oficial"),
        "Câmara — Consumidor": ("https://www.camara.leg.br/noticias/rss/dinamico/CONSUMIDOR","Oficial"),
        "Câmara — Trabalho e Previdência": ("https://www.camara.leg.br/noticias/rss/dinamico/TRABALHO-E-PREVIDENCIA","Oficial"),
        "Câmara — Direitos Humanos": ("https://www.camara.leg.br/noticias/rss/dinamico/DIREITOS-HUMANOS","Oficial"),
        "Migalhas — atualidades jurídicas": ("https://www.migalhas.com.br/rss/rss.xml","Portal jurídico")
    }
    AREA_KEYWORDS = {
        "Consumidor": ["consumidor","compra","fornecedor","negativacao","superendividamento","plano de saude","banco","golpe","cartao de credito","servico defeituoso","cdc","direito do consumidor"],
        "Trabalhista": ["trabalhista","trabalho","empregado","empregador","fgts","clt","jornada","demissao","rescisao","verbas trabalhistas","acidente de trabalho","sindicato","salario"],
        "Previdenciário": ["previdenciario","inss","aposentadoria","beneficio previdenciario","auxilio-doenca","auxilio por incapacidade","bpc","loas","pensao por morte","tempo de contribuicao"],
        "Tributário": ["tributario","imposto","icms","iss","irpf","imposto de renda","reforma tributaria","tributacao","execucao fiscal","credito tributario","carf"],
        "Penal e Processo Penal": ["penal","criminal","crime","prisao","reu","condenacao","inquerito","tribunal do juri","trafico","execucao penal","lei maria da penha","violencia domestica"],
        "Civil e Família": ["direito civil","contrato","familia","divorcio","guarda","pensao alimenticia","heranca","sucessao","responsabilidade civil","dano moral","paternidade","casamento"],
        "Empresarial": ["empresarial","empresa","societario","recuperacao judicial","falencia","insolvencia","sociedade limitada","contrato empresarial","marcas","patentes","propriedade intelectual","cnpj"],
        "Constitucional e Administrativo": ["constitucional","administrativo","servidor publico","licitacao","improbidade","concurso publico","administracao publica","precatorio","stf","controle de constitucionalidade"],
        "Eleitoral": ["eleitoral","eleicao","tse","candidato","propaganda eleitoral","partido politico","voto","urna","fundo eleitoral","inelegibilidade"],
        "Ambiental": ["ambiental","meio ambiente","licenciamento ambiental","ibama","desmatamento","multa ambiental","clima","sustentabilidade"],
        "Digital e LGPD": ["lgpd","dados pessoais","protecao de dados","inteligencia artificial","plataforma digital","algoritmo","ciberseguranca","vazamento de dados","direito digital"],
        "Imobiliário": ["imobiliario","imovel","locacao","aluguel","condominio","usucapiao","registro de imoveis","incorporacao imobiliaria","direito real"],
        "Processo Civil e Jurisprudência": ["processo civil","recurso especial","recurso extraordinario","repetitivo","repercussao geral","jurisprudencia","prescricao","execucao","tutela provisoria","precedente qualificado","sumula"]
    }
    def norm_news(value):
        value=html.unescape(re.sub(r"<[^>]+>"," ",str(value or "")))
        value=unicodedata.normalize("NFKD",value)
        return " ".join("".join(ch for ch in value if not unicodedata.combining(ch)).lower().split())
    def news_areas(title,summary):
        text=norm_news(title+" "+summary)
        counts={area:sum(1 for kw in kws if norm_news(kw) in text) for area,kws in AREA_KEYWORDS.items()}
        return [name for name,score in sorted(counts.items(),key=lambda kv:kv[1],reverse=True) if score>0][:3], counts
    source_names=list(NEWS_SOURCES)
    default_sources=["STJ — notícias oficiais","CNJ — notícias oficiais","Câmara — Direito e Justiça","Migalhas — atualidades jurídicas"]
    col1,col2=st.columns([1.6,1])
    with col1:
        selected_sources=st.multiselect("Fontes",source_names,default=default_sources)
        extra_feeds=st.text_area("Feeds RSS adicionais (opcional, um URL por linha)",height=75,placeholder="https://site.example/feed.xml")
    with col2:
        area_filter=st.selectbox("Área do Direito",["Todas"]+list(AREA_KEYWORDS))
        period=st.selectbox("Publicadas em",["Últimas 24 horas","Últimos 7 dias","Últimos 30 dias","Todas as datas"],index=1)
        search_words=st.text_input("Pesquisar tema, termo ou lei",placeholder="Ex.: LGPD, ICMS, revisão de benefício")
    if st.button("Atualizar radar",type="primary"):
        fetched_entries=[]; seen=set(); errors=[]
        feed_list=[(name,*NEWS_SOURCES[name]) for name in selected_sources]
        for idx,url in enumerate([u.strip() for u in extra_feeds.splitlines() if u.strip()],1):
            feed_list.append((f"Feed personalizado {idx}",url,"Personalizado"))
        for source_name,url,kind in feed_list:
            try:
                response=requests.get(url,timeout=15,headers={"User-Agent":"AdvFlowNewsRadar/1.0"})
                response.raise_for_status()
                parsed=feedparser.parse(response.content)
                if not parsed.entries:
                    errors.append(f"{source_name}: nenhum item RSS foi retornado."); continue
                for entry in parsed.entries[:40]:
                    title=entry.get("title","").strip()
                    link=entry.get("link","").strip()
                    summary=norm_news(entry.get("summary","") or entry.get("description",""))[:650]
                    if not title: continue
                    unique=link or title.casefold()
                    if unique in seen: continue
                    seen.add(unique)
                    parsed_date=entry.get("published_parsed") or entry.get("updated_parsed")
                    published=None
                    if parsed_date:
                        try: published=datetime(*parsed_date[:6])
                        except Exception: published=None
                    inferred,counts=news_areas(title,summary)
                    fetched_entries.append({"fonte":source_name,"tipo_fonte":kind,"titulo":title,"link":link,"resumo":summary,"publicado":published.isoformat(timespec="minutes") if published else "Data não informada","data_obj":published.isoformat() if published else "","areas":inferred,"scores_area":counts})
            except Exception as exc:
                errors.append(f"{source_name}: não foi possível ler o feed ({exc.__class__.__name__}).")
        st.session_state["news_items"]=fetched_entries
        st.session_state["news_errors"]=errors
        st.session_state["news_loaded_at"]=datetime.now().isoformat(timespec="minutes")
    if st.session_state.get("news_loaded_at"):
        st.caption("Última atualização nesta sessão: "+st.session_state["news_loaded_at"])
    for err in st.session_state.get("news_errors",[]):
        st.warning(err)
    if st.session_state.get("news_brief_ready"):
        st.info("Pauta preparada a partir da notícia. Abra a aba Agente de conteúdo; confira a fonte original antes de gerar/publicar.")
    news_items=st.session_state.get("news_items",[])
    if news_items:
        filtered=[]; needle=norm_news(search_words); cutoff=None
        if period=="Últimas 24 horas": cutoff=datetime.now()-timedelta(hours=24)
        elif period=="Últimos 7 dias": cutoff=datetime.now()-timedelta(days=7)
        elif period=="Últimos 30 dias": cutoff=datetime.now()-timedelta(days=30)
        for item in news_items:
            item_date=datetime.fromisoformat(item["data_obj"]) if item.get("data_obj") else None
            if cutoff and (item_date is None or item_date < cutoff): continue
            if area_filter!="Todas" and item.get("scores_area",{}).get(area_filter,0)==0: continue
            searchable=norm_news(item["titulo"]+" "+item["resumo"]+" "+item["fonte"])
            if needle and not all(word in searchable for word in needle.split()): continue
            score=2 if item["tipo_fonte"]=="Oficial" else 1
            if item_date: score += min(3,max(0,3-(datetime.now()-item_date).days//2))
            if area_filter!="Todas": score += min(5,item.get("scores_area",{}).get(area_filter,0)*2)
            if needle: score += sum(1 for word in needle.split() if word in searchable)
            item["relevancia"]=score
            filtered.append(item)
        filtered.sort(key=lambda item:(item["relevancia"],item.get("data_obj","")),reverse=True)
        st.caption(f"{len(filtered)} notícia(s) correspondem aos filtros. Relevância estimada por atualidade, tipo de fonte e correspondência temática.")
        max_items=st.slider("Quantidade exibida",5,40,20,5)
        for index,item in enumerate(filtered[:max_items]):
            area_labels=", ".join(item.get("areas",[])[:3]) or "Área a classificar"
            with st.expander(f'{item["titulo"]} · {item["fonte"]}'):
                c1,c2=st.columns([3,1])
                with c1:
                    st.caption(f'{item["tipo_fonte"]} • {item["publicado"]} • {area_labels}')
                    st.write(item["resumo"] or "O feed não forneceu resumo; abra a fonte para ler o conteúdo.")
                    if item["link"]: st.markdown(f'[Ler na fonte original]({item["link"]})')
                with c2:
                    st.metric("Relevância",item["relevancia"])
                    if st.button("Enviar ao agente",key="news_to_agent_"+str(index)+"_"+hashlib.md5((item["link"] or item["titulo"]).encode()).hexdigest()[:8]):
                        st.session_state["news_brief_subject"]=item["titulo"]
                        st.session_state["news_brief_details"]=f'Notícia: {item["titulo"]}\nFonte: {item["fonte"]}\nData: {item["publicado"]}\nResumo publicado no feed: {item["resumo"]}\nURL original: {item["link"]}\n\nUse a notícia como ponto de partida para uma pauta educativa. Não afirme mudança legal ou tese vinculante sem conferir o documento oficial. Diferencie fato publicado, decisão e interpretação.'
                        st.session_state["brief_version"]=int(st.session_state.get("brief_version",0))+1
                        st.session_state["news_brief_ready"]=True
                        st.rerun()
    else:
        st.info("Clique em Atualizar radar para consultar as fontes. Depois filtre por área, período ou termo.")
    st.caption("Feeds predefinidos: STJ, CNJ, TSE, Agência Câmara e Migalhas. RSS pode ficar indisponível temporariamente. Sempre confira o inteiro teor de decisões, leis e atos em fonte oficial.")

with tabs[5]:
    st.subheader("Calendário editorial")
    with st.form("calform"):
        cd=st.date_input("Data de publicação",value=date.today(),key="cd"); ct=st.time_input("Horário")
        title=st.text_input("Título / pauta"); cf=st.selectbox("Formato",["Carrossel","Reel","Post estático","Story","Artigo"])
        status=st.selectbox("Status",["Ideia","Planejado","Em produção","Em revisão","Aprovado","Publicado"]); notes=st.text_input("Observações")
        add=st.form_submit_button("Adicionar ao calendário")
    if add and title.strip():
        with db() as c:c.execute("INSERT INTO calendar(pub_date,pub_time,title,format,status,notes,user_id) VALUES(?,?,?,?,?,?,?)",(str(cd),str(ct),title,cf,status,notes,user_id))
        st.success("Item adicionado.")
    with db() as c: calendar=[dict(r) for r in c.execute("SELECT * FROM calendar WHERE user_id=? ORDER BY pub_date,pub_time",(user_id,))]
    if calendar:
        dfc=pd.DataFrame(calendar); st.dataframe(dfc,use_container_width=True,hide_index=True)
        st.download_button("Exportar calendário CSV",dfc.to_csv(index=False).encode("utf-8-sig"),"advflow_calendario.csv","text/csv")

with tabs[6]:
    st.subheader("Triagem de publicidade jurídica")
    text_to_check=st.text_area("Cole a legenda, roteiro ou texto da arte",height=220)
    if st.button("Analisar texto"):
        for alert in review(text_to_check):
            if alert.startswith("Nenhum"):st.info(alert)
            else:st.warning(alert)
        st.caption("Triagem lexical preliminar. Não é parecer jurídico nem certificação de conformidade com o Provimento 205/2021 ou outras regras vigentes.")

with tabs[7]:
    st.subheader("Integrações e configuração")
    st.markdown("- **Gemini:** configure GEMINI_API_KEY e um modelo ativo na sua conta; padrão sugerido no código: gemini-3.8-flash.")
    st.markdown("- **Alternativa Groq:** configure GROQ_API_KEY; o padrão sugerido é openai/gpt-oss-120b.")
    st.markdown("- **RSS:** feeds públicos configuráveis.")
    st.markdown("- **Instagram/Meta:** métricas são manuais; publicação, Insights e agendamento não estão conectados.")
    st.markdown("- **Armazenamento:** SQLite local. No Streamlit Cloud, o disco local pode não ser persistente; configure banco externo para uso duradouro.")
    st.markdown("- **Login:** primeiro acesso cria o administrador; ele cria contas na aba Usuários. Senhas recebem hash PBKDF2, cinco falhas bloqueiam o acesso por 15 minutos e cada conta vê apenas seus próprios dados.")
    st.markdown("- **Recuperação:** o administrador redefine a senha; o usuário recebe uma senha temporária e precisa trocá-la. Não há envio de e-mails nesta versão.")
    st.markdown("- **Segurança:** nunca coloque chaves de API nem senhas no GitHub. Configure chaves no painel Secrets do Streamlit.")
    st.markdown("- **Persistência:** o SQLite pode não sobreviver a reinícios/redeploys de algumas hospedagens. Configure banco persistente e backup antes de usar em produção.")
    st.markdown("Links: [Gemini API](https://aistudio.google.com/apikey) · [Groq](https://console.groq.com/keys) · [Streamlit Community Cloud](https://share.streamlit.io)")


if current_user["role"] == "admin":
    with tabs[8]:
        st.subheader("Gestão de usuários")
        st.caption("Crie contas individuais para a equipe. A senha temporária é exibida no momento da criação ou redefinição.")
        with st.form("create_user_form"):
            new_username=st.text_input("Novo nome de usuário",placeholder="ex.: redacao")
            role_admin=st.checkbox("Dar permissão de administrador")
            create_user_submit=st.form_submit_button("Criar conta",type="primary")
        if create_user_submit:
            clean_new=(new_username or "").strip().lower()
            if not valid_username(clean_new):
                st.error("Use 3 a 32 caracteres: letras, números, ponto, hífen ou sublinhado.")
            else:
                temporary_password=secrets.token_urlsafe(12)
                salt,digest=password_record(temporary_password)
                try:
                    with db() as c:
                        c.execute("INSERT INTO users(username,password_salt,password_hash,role,active,must_change_password,created_at) VALUES(?,?,?,?,?,?,?)",
                                  (clean_new,salt,digest,"admin" if role_admin else "user",1,1,datetime.now().isoformat(timespec="seconds")))
                    st.session_state["temporary_credentials"]={"username":clean_new,"password":temporary_password,"message":"Conta criada. Copie a senha temporária agora; ela será ocultada ao sair desta tela."}
                except sqlite3.IntegrityError:
                    st.error("Esse nome de usuário já existe.")
        if st.session_state.get("temporary_credentials"):
            creds=st.session_state["temporary_credentials"]
            st.warning(creds["message"])
            st.code("Usuário: "+creds["username"]+"\nSenha temporária: "+creds["password"])
            if st.button("Ocultar credencial temporária",key="hide_temporary_credentials"):
                st.session_state.pop("temporary_credentials",None)
                st.rerun()
        st.divider()
        with db() as c:
            account_rows=[dict(r) for r in c.execute("SELECT username,role,active,must_change_password,failed_attempts,locked_until,created_at FROM users ORDER BY username")]
        if account_rows:
            view=pd.DataFrame(account_rows)
            view["status"]=view["active"].map({1:"Ativa",0:"Desativada"})
            view["senha temporária pendente"]=view["must_change_password"].map({1:"Sim",0:"Não"})
            st.dataframe(view[["username","role","status","senha temporária pendente","failed_attempts","locked_until","created_at"]],use_container_width=True,hide_index=True)
            choices=[r["username"] for r in account_rows]
            selected_user=st.selectbox("Gerenciar conta",choices)
            target=next(r for r in account_rows if r["username"]==selected_user)
            b1,b2,b3=st.columns(3)
            reset_clicked=b1.button("Redefinir senha",disabled=(selected_user==current_user["username"]),key="reset_user_password")
            if reset_clicked:
                temp=secrets.token_urlsafe(12); salt,digest=password_record(temp)
                with db() as c:
                    c.execute("UPDATE users SET password_salt=?,password_hash=?,must_change_password=1,failed_attempts=0,locked_until=NULL WHERE username=?",(salt,digest,selected_user))
                st.session_state["temporary_credentials"]={"username":selected_user,"password":temp,"message":"Senha redefinida. Entregue por canal seguro; o usuário deverá trocá-la."}
                st.rerun()
            if selected_user==current_user["username"]:
                st.caption("Para sua própria conta, use a alteração de senha no primeiro acesso ou peça a outro administrador para redefini-la.")
            if target["username"]==current_user["username"]:
                b2.button("Desativar conta",disabled=True,key="disable_self")
                b3.button("Reativar conta",disabled=True,key="enable_self")
            else:
                if target["active"]:
                    if b2.button("Desativar conta",key="disable_user"):
                        if target["role"]=="admin" and sum(1 for r in account_rows if r["role"]=="admin" and r["active"])<=1:
                            st.error("Não é possível desativar o último administrador.")
                        else:
                            with db() as c:c.execute("UPDATE users SET active=0 WHERE username=?",(selected_user,))
                            st.success("Conta desativada."); st.rerun()
                    b3.button("Reativar conta",disabled=True,key="reactivate_disabled")
                else:
                    b2.button("Desativar conta",disabled=True,key="disable_disabled")
                    if b3.button("Reativar conta",key="enable_user"):
                        with db() as c:c.execute("UPDATE users SET active=1,failed_attempts=0,locked_until=NULL WHERE username=?",(selected_user,))
                        st.success("Conta reativada."); st.rerun()
