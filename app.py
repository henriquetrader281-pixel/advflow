import os, re, json, sqlite3, io, hashlib, secrets, html, unicodedata, zipfile, textwrap, base64
from datetime import datetime, date, timedelta
import requests, feedparser, pandas as pd, streamlit as st
from PIL import Image, ImageDraw, ImageFont, ImageOps

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
    c.execute("CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY, post_date TEXT, topic TEXT, format TEXT, reach INTEGER, likes INTEGER, comments INTEGER, saves INTEGER, shares INTEGER, user_id TEXT NOT NULL DEFAULT 'legacy', funnel_stage TEXT NOT NULL DEFAULT 'Descoberta', profile_visits INTEGER NOT NULL DEFAULT 0, link_clicks INTEGER NOT NULL DEFAULT 0, data_completeness TEXT NOT NULL DEFAULT 'Parcial', objective TEXT NOT NULL DEFAULT 'Interações úteis')")
    c.execute("CREATE TABLE IF NOT EXISTS metric_targets(user_id TEXT NOT NULL, metric_key TEXT NOT NULL, target_value REAL NOT NULL, warning_value REAL NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(user_id, metric_key))")
    c.execute("CREATE TABLE IF NOT EXISTS calendar(id INTEGER PRIMARY KEY, pub_date TEXT, pub_time TEXT, title TEXT, format TEXT, status TEXT, notes TEXT, user_id TEXT NOT NULL DEFAULT 'legacy')")
    migrations = {
        "users": {"role":"TEXT NOT NULL DEFAULT 'user'","active":"INTEGER NOT NULL DEFAULT 1","must_change_password":"INTEGER NOT NULL DEFAULT 0","failed_attempts":"INTEGER NOT NULL DEFAULT 0","locked_until":"TEXT","created_at":"TEXT"},
        "pieces": {"user_id":"TEXT NOT NULL DEFAULT 'legacy'"},
        "metrics": {"user_id":"TEXT NOT NULL DEFAULT 'legacy'","funnel_stage":"TEXT NOT NULL DEFAULT 'Descoberta'","profile_visits":"INTEGER NOT NULL DEFAULT 0","link_clicks":"INTEGER NOT NULL DEFAULT 0","data_completeness":"TEXT NOT NULL DEFAULT 'Parcial'","objective":"TEXT NOT NULL DEFAULT 'Interações úteis'"},
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

SYSTEM = """Você é diretor de criação e estrategista sênior de conteúdo para advocacia brasileira. Produza copy editorial de nível profissional: específica, fluida, persuasiva pelo valor informativo (nunca por pressão comercial), com raciocínio progressivo e linguagem acessível para o público solicitado. Evite clichês, introduções vazias, repetição entre slides, frases genéricas do tipo 'é importante conhecer', perguntas artificiais, promessas de resultado, sensacionalismo, comparação, autoengrandecimento, captação de clientela e estímulo ao litígio. Não invente leis, artigos, prazos, estatísticas, decisões, fatos ou fontes. Quando o contexto estiver incompleto, use formulação condicional e indique o que precisa ser confirmado. O texto deve conter uma ideia concreta por slide, título editorial curto, parágrafo objetivo e transição lógica até a conclusão. Adeque profundidade, vocabulário, exemplos e objeções à área e ao segmento escolhidos. Diferencie informação geral de aconselhamento individual. Respeite a publicidade informativa, sóbria e discreta exigida pela OAB; não use CTA que induza contratação. A etapa de funil define intenção editorial, não uma promessa de conversão comercial.

Retorne JSON válido com as chaves:
titulo, promessa_editorial, angulo_editorial, publico, area_direito, etapa_funil, resumo_estrategico, gancho, slides (lista de objetos titulo/texto/visual/objetivo_do_slide), roteiro (lista de cena/fala/visual), legenda, cta_educativa, hashtags, fontes_para_conferir, checklist_revisao.
Carrossel: 6 a 8 slides e progressão gancho-contexto-explicação-aplicação-cuidados-síntese. Reel: roteiro para 30–45 segundos, cenas com fala natural, indicação de texto na tela e plano visual. Post estático: uma única ideia clara. Legenda: texto completo, sem slides. O CTA deve corresponder ao funil e ser educativo ou institucional (por exemplo, salvar, compartilhar informação, consultar fonte oficial, acompanhar conteúdos ou conhecer informações institucionais); nunca pressionar a contratação.""" 
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

FUNNEL_STAGES = [
    "Descoberta | ampliar alcance qualificado",
    "Consideração | aprofundar compreensão",
    "Relacionamento | construir recorrência e confiança",
    "Conversão editorial | oferecer próximo passo informativo"
]
AUDIENCE_OPTIONS = [
    "Público geral sem formação jurídica",
    "Consumidores e famílias",
    "Trabalhadores e profissionais CLT",
    "Empregadores, RH e gestores",
    "Microempreendedores e pequenas empresas",
    "Empresas e departamentos jurídicos",
    "Segurados do INSS e familiares",
    "Famílias em decisões patrimoniais",
    "Compradores, vendedores e locadores de imóveis",
    "Profissionais liberais e autônomos",
    "Servidores e candidatos a concursos",
    "Advogados e estudantes de Direito",
    "Outro segmento específico"
]
LEGAL_AREAS = [
    "Direito do Consumidor","Direito do Trabalho","Direito Previdenciário","Direito de Família e Sucessões",
    "Direito Empresarial","Direito Tributário","Direito Civil e Contratos","Direito Imobiliário",
    "Direito Penal","Direito Administrativo","Direito Digital e LGPD","Direito Médico e da Saúde",
    "Direito Ambiental","Propriedade Intelectual","Direito Eleitoral","Geral / interdisciplinar"
]
TONE_OPTIONS = ["Didático e direto","Analítico e técnico, mas acessível","Acolhedor sem alarmismo","Executivo para empresários","Editorial sofisticado","Pergunta e resposta"]
ANGLE_OPTIONS = [
    "Dúvida frequente explicada","Erro comum e como evitar","Mito versus realidade","Passo a passo prático",
    "O que mudou e o que conferir","Checklist de prevenção","Cenário hipotético claramente identificado",
    "Leitura de decisão ou notícia","Comparação de conceitos sem comparar profissionais"
]
CTA_OPTIONS = [
    "Salvar como referência","Compartilhar informação útil","Consultar fonte oficial",
    "Acompanhar a série de conteúdos","Conhecer informações institucionais",
    "Enviar uma dúvida geral para pauta futura"
]

def local_draft(subject, fmt, audience, goal, area="Geral / interdisciplinar", funnel_stage=FUNNEL_STAGES[0], tone=TONE_OPTIONS[0], angle=ANGLE_OPTIONS[0], requested_cta=CTA_OPTIONS[0], details=""):
    stage=(funnel_stage.split("|")[0] if "|" in funnel_stage else funnel_stage).strip()
    cta_map={
        "Descoberta":"Compartilhe este conteúdo informativo com quem possa considerá-lo útil.",
        "Consideração":"Salve o checklist e confira as referências oficiais antes de tomar decisões.",
        "Relacionamento":"Acompanhe os próximos conteúdos da série para aprofundar o tema.",
        "Conversão editorial":"Consulte as informações institucionais e as fontes oficiais para entender os próximos passos disponíveis."
    }
    cta=cta_map.get(stage,"Salve e consulte as fontes oficiais.")
    titles = [
        f"O que está em jogo em {subject}?",
        "O conceito que muda a leitura",
        "Como a regra funciona em linhas gerais",
        "Quais pontos precisam ser verificados",
        "Onde surgem as dúvidas mais comuns",
        "Como consultar a fonte correta",
        "O que levar desta explicação"
    ]
    texts = [
        f"Antes de tirar uma conclusão sobre {subject}, é preciso separar a regra geral dos detalhes de cada situação. Este conteúdo organiza os principais pontos para {audience.lower()}.",
        f"Na área de {area}, termos parecidos podem ter efeitos diferentes. A análise começa pela definição aplicável e pelo contexto, não apenas pelo nome que aparece em um documento.",
        "O caminho de leitura é: identificar a situação, localizar a regra aplicável e conferir os requisitos e exceções na fonte primária. A aplicação concreta depende dos fatos e da norma vigente.",
        "Confira datas, documentos, destinatário da regra, eventuais exceções e se houve alteração normativa ou decisão posterior. Não conclua que um direito ou obrigação existe apenas com base em um resumo.",
        "A dúvida recorrente costuma surgir quando se transforma uma regra geral em resposta automática. Sem os fatos relevantes, a explicação deve permanecer geral e condicional.",
        "Priorize legislação atualizada, páginas oficiais de tribunais e órgãos competentes e o inteiro teor de decisões citadas. Notícias ajudam a encontrar o assunto, mas não substituem a fonte primária.",
        f"Para {goal.lower()}, o próximo passo informativo é consultar as referências, identificar quais fatos ainda faltam e evitar conclusões universais. {cta}"
    ]
    slides=[{"titulo":t,"texto":v,"visual":f"Direção editorial ilustrada relacionada a {area}; hierarquia tipográfica e ícone temático.","objetivo_do_slide":("gancho" if i==0 else "explicação" if i<5 else "síntese")} for i,(t,v) in enumerate(zip(titles,texts))]
    if fmt=="Carrossel": slides=slides[:7]
    elif fmt=="Post estático": slides=[{"titulo":subject,"texto":texts[0]+" "+texts[6],"visual":f"Cartaz editorial de {area}, com ilustração temática.","objetivo_do_slide":"síntese"}]
    elif fmt in ("Legenda","Reel"): slides=[]
    script=[
        {"cena":"Gancho (0–4 s)","fala":f"Uma dúvida comum sobre {subject} é confundir a regra geral com o que depende dos fatos.","visual":f"Plano médio; título visual associado a {area}; texto curto na tela."},
        {"cena":"Explicação (4–16 s)","fala":f"Para entender o tema, comece pelo conceito e identifique qual regra realmente se aplica ao contexto de {audience.lower()}.","visual":"Corte para duas ou três palavras-chave e uma ilustração editorial específica."},
        {"cena":"Aplicação (16–32 s)","fala":"Confira os requisitos, as exceções e a data da norma. Um resumo ou uma manchete não substitui a consulta à fonte original.","visual":"Lista visual de três verificações, sem ícones genéricos de banco de imagens."},
        {"cena":"Fechamento (32–42 s)","fala":cta+" Informação geral; cada situação exige avaliação dos fatos e das regras aplicáveis.","visual":"Síntese com identidade visual e referência para conferência."}
    ] if fmt=="Reel" else []
    caption=(f"{subject}\n\n{texts[0]}\n\n{texts[2]}\n\n{texts[3]}\n\n{cta}\n\nConteúdo informativo geral. Confira as fontes oficiais e considere as particularidades de cada situação.")
    if fmt=="Legenda": caption=caption
    hashtags=["#InformaçãoJurídica","#Direito", "#"+re.sub(r"[^A-Za-z0-9]","",area.title().replace("Direito",""))[:22]]
    return {
        "titulo":subject,"promessa_editorial":f"Explicar {subject} com clareza para {audience}.","angulo_editorial":angle,
        "publico":audience,"area_direito":area,"etapa_funil":stage,"resumo_estrategico":f"{goal}: conteúdo {stage.lower()}, em tom {tone.lower()}, com foco em {audience}.",
        "gancho":texts[0],"slides":slides,"roteiro":script,"legenda":caption,"cta_educativa":cta,
        "hashtags":hashtags,"fontes_para_conferir":["Texto legal oficial e atualizado aplicável ao tema","Página oficial do tribunal ou órgão competente; conferir data, contexto e inteiro teor da decisão, se houver"],
        "checklist_revisao":["Confirmar todos os fundamentos jurídicos em fonte primária","Revisar a clareza para o público escolhido","Conferir sobriedade e caráter informativo conforme as regras da OAB"]
    }
def error_detail(response):
    try:
        payload=response.json()
        if isinstance(payload,dict):
            err=payload.get("error",payload)
            if isinstance(err,dict):
                return str(err.get("message") or err.get("status") or err)[:500]
            return str(err)[:500]
    except Exception:
        pass
    return (response.text or "sem detalhe fornecido pela API")[:500]

def model_candidates(configured, defaults):
    clean=(configured or "").strip().strip('"').strip("'")
    if clean.startswith("models/"):
        clean=clean[len("models/"):]
    candidates=[]
    for item in [clean]+list(defaults):
        item=(item or "").strip().strip('"').strip("'")
        if item.startswith("models/"):
            item=item[len("models/"):]
        if item and item not in candidates:
            candidates.append(item)
    return candidates

def call_gemini(prompt):
    key=secret("GEMINI_API_KEY")
    if not key: raise RuntimeError("GEMINI_API_KEY não configurada nos Secrets do Streamlit")
    configured=secret("GEMINI_MODEL","gemini-3.8-flash")
    candidates=model_candidates(configured,["gemini-3.8-flash","gemini-3.7-flash","gemini-3.6-flash","gemini-3.5-flash-lite"])
    failures=[]
    for model in candidates:
        try:
            r=requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",params={"key":key},
              json={"systemInstruction":{"parts":[{"text":SYSTEM}]},"contents":[{"role":"user","parts":[{"text":prompt}]}],"generationConfig":{"temperature":0.55,"responseMimeType":"application/json"}},timeout=60)
            if not r.ok:
                detail=error_detail(r)
                failures.append(f"{model}: HTTP {r.status_code} ({detail})")
                if r.status_code in (400,404,422):
                    continue
                raise RuntimeError(f"Gemini HTTP {r.status_code}: {detail}")
            payload=json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
            payload["_modelo_utilizado"]=model
            return payload
        except requests.RequestException as exc:
            failures.append(f"{model}: falha de conexão ({exc.__class__.__name__})")
    raise RuntimeError("Nenhum modelo Gemini funcionou. " + " | ".join(failures[:4]))

def call_groq(prompt):
    key=secret("GROQ_API_KEY")
    if not key: raise RuntimeError("GROQ_API_KEY não configurada nos Secrets do Streamlit")
    configured=secret("GROQ_MODEL","openai/gpt-oss-120b")
    candidates=model_candidates(configured,["openai/gpt-oss-120b","openai/gpt-oss-20b"])
    failures=[]
    for model in candidates:
        try:
            r=requests.post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":"Bearer "+key},
              json={"model":model,"temperature":0.55,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":prompt}]},timeout=60)
            if not r.ok:
                detail=error_detail(r)
                failures.append(f"{model}: HTTP {r.status_code} ({detail})")
                if r.status_code in (400,404,422):
                    continue
                raise RuntimeError(f"Groq HTTP {r.status_code}: {detail}")
            payload=json.loads(r.json()["choices"][0]["message"]["content"])
            payload["_modelo_utilizado"]=model
            return payload
        except requests.RequestException as exc:
            failures.append(f"{model}: falha de conexão ({exc.__class__.__name__})")
    raise RuntimeError("Nenhum modelo Groq funcionou. " + " | ".join(failures[:3]))
def parse_json_response(content):
    """Parse JSON returned by chat-completion providers, tolerating fenced output."""
    text_value=(content or "").strip()
    text_value=re.sub(r"^(?:\x60{3})(?:json)?\s*|\s*(?:\x60{3})$", "", text_value, flags=re.I).strip()
    try:
        return json.loads(text_value)
    except json.JSONDecodeError:
        start=text_value.find("{"); end=text_value.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text_value[start:end+1])
        raise RuntimeError("O modelo não retornou um objeto JSON válido. Tente outro modelo no OpenRouter.")

def call_openrouter(prompt):
    key=secret("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY não configurada nos Secrets do Streamlit")
    configured=secret("OPENROUTER_MODEL","openrouter/free")
    candidates=model_candidates(configured,["openrouter/free"])
    failures=[]
    for model in candidates:
        try:
            response=requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization":"Bearer "+key,
                    "Content-Type":"application/json",
                    "HTTP-Referer":"https://github.com/henriquetrader281-pixel/advflow",
                    "X-OpenRouter-Title":"AdvFlow"
                },
                json={
                    "model":model,
                    "temperature":0.55,
                    "max_tokens":6500,
                    "messages":[
                        {"role":"system","content":SYSTEM},
                        {"role":"user","content":prompt+"\n\nRetorne apenas um JSON válido, sem markdown ou comentários."}
                    ]
                },
                timeout=90
            )
            if not response.ok:
                detail=error_detail(response)
                failures.append(f"{model}: HTTP {response.status_code} ({detail})")
                if response.status_code in (400,404,422):
                    continue
                raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {detail}")
            payload=response.json()
            choices=payload.get("choices") or []
            if not choices:
                failures.append(f"{model}: resposta sem choices")
                continue
            message=choices[0].get("message") or {}
            content=message.get("content")
            if isinstance(content,list):
                content="\n".join(str(part.get("text","")) for part in content if isinstance(part,dict))
            result=parse_json_response(content or "")
            if not isinstance(result,dict):
                failures.append(f"{model}: resposta não é objeto JSON")
                continue
            result["_modelo_utilizado"]=model
            result["_provedor_utilizado"]="OpenRouter"
            return result
        except requests.RequestException as exc:
            failures.append(f"{model}: falha de conexão ({exc.__class__.__name__})")
        except (KeyError,IndexError,ValueError,json.JSONDecodeError) as exc:
            failures.append(f"{model}: resposta inválida ({exc.__class__.__name__})")
    raise RuntimeError("Nenhum modelo OpenRouter funcionou. "+" | ".join(failures[:3]))

def test_openrouter_connection():
    key=secret("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY não configurada nos Secrets do Streamlit")
    model=secret("OPENROUTER_MODEL","openrouter/free").strip() or "openrouter/free"
    response=requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization":"Bearer "+key,
            "Content-Type":"application/json",
            "HTTP-Referer":"https://github.com/henriquetrader281-pixel/advflow",
            "X-OpenRouter-Title":"AdvFlow"
        },
        json={"model":model,"max_tokens":12,"messages":[{"role":"user","content":"Responda apenas: OK"}]},
        timeout=45
    )
    if not response.ok:
        raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {error_detail(response)}")
    data=response.json()
    if not data.get("choices"):
        raise RuntimeError("OpenRouter respondeu, mas não retornou choices.")
    return model

def generate(subject, fmt, audience, goal, details="", area="Geral / interdisciplinar", funnel_stage=FUNNEL_STAGES[0], tone=TONE_OPTIONS[0], angle=ANGLE_OPTIONS[0], requested_cta=CTA_OPTIONS[0]):
    brief=json.dumps({"assunto":subject,"area_do_direito":area,"publico_segmentado":audience,"formato":fmt,"objetivo_editorial":goal,"etapa_do_funil":funnel_stage,"tom_de_voz":tone,"angulo_editorial":angle,"preferencia_de_cta_educativa":requested_cta,"paleta_visual":st.session_state.get("palette_choice","Azul-marinho e dourado"),"contexto_fontes_e_restricoes":details},ensure_ascii=False)
    errors=[]
    for name,fn in [("Gemini",call_gemini),("Groq (alternativo)",call_groq),("OpenRouter (alternativo)",call_openrouter)]:
        try: return fn("Crie a peça a partir deste briefing. Não invente referências jurídicas. "+brief),name
        except Exception as e: errors.append(name+": "+str(e))
    result=local_draft(subject,fmt,audience,goal,area,funnel_stage,tone,angle,requested_cta,details)
    result["aviso"]="IA externa indisponível; estrutura local usada. "+" | ".join(errors)
    return result,"Modelo local"

PALETTES={
    "Azul-marinho & champanhe":("#F5F1E8","#172842","#B79B61"),
    "Verde-pinho & marfim":("#F2F4EE","#183F38","#829C83"),
    "Vinho & rosa mineral":("#F7F0F0","#492433","#BA8794"),
    "Grafite & prata":("#F2F4F7","#252B36","#8A9AAF"),
    "Azul petróleo & cobre":("#EEF4F4","#123F49","#C17A52"),
    "Terracota & areia":("#F7F0E7","#653E34","#C17C63"),
    "Azul real & gelo":("#EFF4FB","#173C75","#628FD0"),
    "Oliva & papel":("#F5F3E8","#41492C","#9A9A64"),
    "Ameixa & lavanda":("#F5F0F7","#40264B","#A78AB7"),
    "Personalizada":("#F5F1E8","#172842","#B79B61")
}
def _rgb(hex_colour):
    value=hex_colour.lstrip("#")
    return tuple(int(value[i:i+2],16) for i in (0,2,4))

def _blend(color_a,color_b,ratio):
    a=_rgb(color_a) if isinstance(color_a,str) else color_a
    b=_rgb(color_b) if isinstance(color_b,str) else color_b
    return tuple(int(a[i]*(1-ratio)+b[i]*ratio) for i in range(3))

def _topic_icon(area):
    value=unicodedata.normalize("NFKD",str(area)).encode("ascii","ignore").decode("ascii").lower()
    if "consum" in value: return "protection"
    if "trabalho" in value: return "briefcase"
    if "previd" in value: return "shield"
    if "famil" in value or "sucess" in value: return "home"
    if "empres" in value: return "building"
    if "tribut" in value: return "calculator"
    if "imobili" in value: return "architecture"
    if "digital" in value or "lgpd" in value: return "network"
    if "penal" in value: return "shield"
    if "eleitoral" in value: return "ballot"
    if "ambient" in value: return "leaf"
    if "saude" in value or "medico" in value: return "cross"
    if "propriedade" in value or "intelectual" in value: return "document"
    return "scales"

def _draw_icon(draw, kind, cx, cy, unit, ink, accent, light):
    w=max(5,int(unit*0.045)); u=unit
    def line(points,fill=ink,width=None):
        draw.line(points,fill=fill,width=width or w,joint="curve")
    if kind=="scales":
        line([(cx,cy-u*.48),(cx,cy+u*.37)],ink)
        line([(cx-u*.34,cy-u*.30),(cx+u*.34,cy-u*.30)],ink)
        line([(cx-u*.24,cy-u*.30),(cx-u*.34,cy-u*.03)],accent)
        line([(cx-u*.42,cy-u*.03),(cx-u*.26,cy-u*.03)],accent)
        line([(cx-u*.34,cy-u*.03),(cx-u*.45,cy+u*.15),(cx-u*.23,cy+u*.15),(cx-u*.34,cy-u*.03)],accent)
        line([(cx+u*.24,cy-u*.30),(cx+u*.34,cy-u*.03)],accent)
        line([(cx+u*.26,cy-u*.03),(cx+u*.45,cy-u*.03)],accent)
        line([(cx+u*.34,cy-u*.03),(cx+u*.23,cy+u*.15),(cx+u*.45,cy+u*.15),(cx+u*.34,cy-u*.03)],accent)
        line([(cx-u*.17,cy+u*.37),(cx+u*.17,cy+u*.37)],ink)
        line([(cx-u*.25,cy+u*.44),(cx+u*.25,cy+u*.44)],ink)
    elif kind in ("building","architecture"):
        draw.rectangle((cx-u*.32,cy-u*.42,cx+u*.32,cy+u*.39),outline=ink,width=w)
        line([(cx-u*.39,cy-u*.42),(cx,cy-u*.55),(cx+u*.39,cy-u*.42)],accent)
        for row in range(3):
            for col in range(3):
                x=cx-u*.21+col*u*.21; y=cy-u*.29+row*u*.21
                draw.rounded_rectangle((x-u*.045,y-u*.055,x+u*.045,y+u*.055),radius=2,fill=accent)
        draw.rectangle((cx-u*.07,cy+u*.20,cx+u*.07,cy+u*.39),fill=light)
    elif kind=="briefcase":
        draw.rounded_rectangle((cx-u*.39,cy-u*.15,cx+u*.39,cy+u*.31),radius=int(u*.06),outline=ink,width=w)
        draw.arc((cx-u*.19,cy-u*.35,cx+u*.19,cy+u*.02),180,360,fill=ink,width=w)
        line([(cx-u*.39,cy+u*.02),(cx+u*.39,cy+u*.02)],accent)
        draw.rectangle((cx-u*.05,cy-u*.02,cx+u*.05,cy+u*.08),fill=accent)
    elif kind=="protection" or kind=="shield":
        pts=[(cx,cy-u*.48),(cx+u*.34,cy-u*.32),(cx+u*.29,cy+u*.10),(cx,cy+u*.43),(cx-u*.29,cy+u*.10),(cx-u*.34,cy-u*.32),(cx,cy-u*.48)]
        draw.polygon(pts,fill=light)
        line(pts,ink)
        line([(cx-u*.16,cy),(cx-u*.03,cy+u*.13),(cx+u*.19,cy-u*.13)],accent,max(w,int(u*.06)))
        if kind=="protection":
            draw.arc((cx-u*.17,cy-u*.13,cx+u*.17,cy+u*.19),180,360,fill=accent,width=w)
    elif kind=="home":
        draw.polygon([(cx-u*.42,cy-u*.08),(cx,cy-u*.43),(cx+u*.42,cy-u*.08)],fill=light)
        line([(cx-u*.42,cy-u*.08),(cx,cy-u*.43),(cx+u*.42,cy-u*.08)],ink)
        draw.rectangle((cx-u*.31,cy-u*.07,cx+u*.31,cy+u*.34),outline=ink,width=w)
        draw.rectangle((cx-u*.07,cy+u*.11,cx+u*.07,cy+u*.34),fill=accent)
        draw.rectangle((cx-u*.23,cy, cx-u*.12,cy+u*.11),fill=accent)
        draw.rectangle((cx+u*.12,cy,cx+u*.23,cy+u*.11),fill=accent)
    elif kind=="calculator":
        draw.rounded_rectangle((cx-u*.30,cy-u*.45,cx+u*.30,cy+u*.44),radius=int(u*.06),outline=ink,width=w)
        draw.rounded_rectangle((cx-u*.20,cy-u*.34,cx+u*.20,cy-u*.17),radius=int(u*.02),fill=light,outline=accent,width=max(3,w//2))
        for row in range(3):
            for col in range(3):
                x=cx-u*.19+col*u*.19; y=cy-u*.06+row*u*.16
                draw.rounded_rectangle((x-u*.045,y-u*.045,x+u*.045,y+u*.045),radius=2,fill=accent if row==2 and col==2 else light,outline=accent,width=2)
    elif kind=="network":
        pts=[(cx,cy-u*.32),(cx-u*.30,cy-u*.12),(cx+u*.30,cy-u*.12),(cx-u*.23,cy+u*.25),(cx+u*.23,cy+u*.25),(cx,cy+u*.02)]
        for i,j in [(0,1),(0,2),(1,3),(1,5),(2,4),(2,5),(3,4),(4,5)]:
            line([pts[i],pts[j]],_blend(ink,light,.10),max(3,w//2))
        for k,p in enumerate(pts):
            rad=u*(.055 if k else .085)
            draw.ellipse((p[0]-rad,p[1]-rad,p[0]+rad,p[1]+rad),fill=accent if k in (0,3) else light,outline=ink,width=w)
    elif kind=="ballot":
        draw.rounded_rectangle((cx-u*.34,cy-u*.42,cx+u*.34,cy+u*.4),radius=int(u*.04),outline=ink,width=w)
        draw.rectangle((cx-u*.20,cy-u*.27,cx-u*.04,cy-u*.11),outline=accent,width=w)
        line([(cx-u*.18,cy-u*.18),(cx-u*.12,cy-u*.12),(cx-u*.03,cy-u*.24)],accent)
        for yy in [cy,cy+u*.16]:
            line([(cx-u*.19,yy),(cx+u*.19,yy)],ink,max(3,w//2))
    elif kind=="leaf":
        draw.ellipse((cx-u*.28,cy-u*.45,cx+u*.28,cy+u*.25),outline=ink,width=w)
        line([(cx-u*.22,cy+u*.26),(cx+u*.20,cy-u*.29)],accent)
        line([(cx-u*.06,cy+u*.02),(cx+u*.18,cy+u*.01)],accent,max(3,w//2))
        line([(cx-u*.01,cy-u*.10),(cx-u*.18,cy-u*.13)],accent,max(3,w//2))
    elif kind=="cross":
        draw.rounded_rectangle((cx-u*.36,cy-u*.36,cx+u*.36,cy+u*.36),radius=int(u*.08),outline=ink,width=w)
        draw.rectangle((cx-u*.065,cy-u*.23,cx+u*.065,cy+u*.23),fill=accent)
        draw.rectangle((cx-u*.23,cy-u*.065,cx+u*.23,cy+u*.065),fill=accent)
    else:
        draw.rounded_rectangle((cx-u*.30,cy-u*.43,cx+u*.30,cy+u*.43),radius=int(u*.03),outline=ink,width=w)
        line([(cx-u*.18,cy-u*.22),(cx+u*.18,cy-u*.22)],accent)
        line([(cx-u*.18,cy-u*.05),(cx+u*.18,cy-u*.05)],ink,max(3,w//2))
        line([(cx-u*.18,cy+u*.12),(cx+u*.10,cy+u*.12)],ink,max(3,w//2))
        line([(cx-u*.18,cy+u*.28),(cx+u*.18,cy+u*.28)],accent)

def make_art(title, body, palette, signature="", area="Geral / interdisciplinar", style="Editorial premium", slide_index=1, total_slides=1, funnel_stage="Descoberta", logo_bytes=None):
    bg,ink,accent=PALETTES[palette]
    bg_rgb=_rgb(bg); ink_rgb=_rgb(ink); accent_rgb=_rgb(accent)
    im=Image.new("RGB",(1080,1350),bg_rgb); d=ImageDraw.Draw(im)
    for y in range(1350):
        t=y/1349; colour=_blend(bg_rgb,_blend(bg_rgb,accent_rgb,.18),t*.72); d.line((0,y,1080,y),fill=colour)
    def font(sz,bold=False):
        paths=(["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf","C:/Windows/Fonts/arialbd.ttf"] if bold else ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","C:/Windows/Fonts/arial.ttf"])
        for path in paths:
            try:return ImageFont.truetype(path,sz)
            except Exception:pass
        return ImageFont.load_default()
    soft=_blend(bg_rgb,accent_rgb,.15); very_soft=_blend(bg_rgb,accent_rgb,.075)
    d.rectangle((0,0,1080,18),fill=accent_rgb); d.ellipse((650,-190,1230,390),fill=soft); d.ellipse((760,-95,1140,285),outline=accent_rgb,width=3)
    icon_kind=_topic_icon(area); stage_short=(funnel_stage.split("|")[0].strip() if "|" in funnel_stage else funnel_stage).upper()
    d.rounded_rectangle((70,56,675,102),radius=18,fill=very_soft,outline=accent_rgb,width=1)
    d.text((91,68),f"{area.upper()}  /  {stage_short}"[:47],font=font(18,True),fill=ink_rgb)
    _draw_icon(d,icon_kind,850,245,250,ink_rgb,accent_rgb,bg_rgb)
    def wrapped(txt,x,y,ft,max_width,color,leading,max_lines=8):
        words=str(txt or "").split(); lines=[]; line=""
        for word in words:
            candidate=(line+" "+word).strip()
            if d.textbbox((0,0),candidate,font=ft)[2] <= max_width or not line:line=candidate
            else:lines.append(line);line=word
        if line:lines.append(line)
        for line in lines[:max_lines]:d.text((x,y),line,font=ft,fill=color);y+=leading
        if len(lines)>max_lines:d.text((x,y),"…",font=ft,fill=color);y+=leading
        return y
    if slide_index==1:
        y=wrapped(title,72,470,font(68 if len(str(title))<43 else 57,True),930,ink_rgb,78,4)
        d.rounded_rectangle((72,y+12,280,y+19),radius=3,fill=accent_rgb)
        wrapped(body,72,y+53,font(31),900,ink_rgb,43,8)
    else:
        d.text((72,160),f"IDEIA {slide_index:02d} / {max(total_slides,1):02d}",font=font(19,True),fill=accent_rgb)
        y=wrapped(title,72,208,font(58 if len(str(title))<42 else 49,True),910,ink_rgb,66,4)
        d.rounded_rectangle((72,y+10,320,y+17),radius=3,fill=accent_rgb);y+=45
        d.rounded_rectangle((54,y-12,1025,1110),radius=26,outline=_blend(accent_rgb,bg_rgb,.55),width=2)
        d.ellipse((68,y+1,88,y+21),fill=accent_rgb)
        wrapped(body,86,y+45,font(32),855,ink_rgb,45,13)
    d.line((72,1204,1008,1204),fill=_blend(accent_rgb,bg_rgb,.36),width=2)
    d.text((72,1225),(signature or "CONTEÚDO INFORMATIVO")[:45],font=font(20,True),fill=ink_rgb)
    d.text((72,1266),"Informação geral • confira as fontes e o contexto aplicável",font=font(17),fill=_blend(ink_rgb,bg_rgb,.2))
    d.text((935,1302),f"{slide_index:02d} / {max(total_slides,1):02d}",font=font(17,True),fill=accent_rgb)
    if logo_bytes:
        try:
            logo=Image.open(io.BytesIO(logo_bytes)).convert("RGBA");logo.thumbnail((150,70))
            im_rgba=im.convert("RGBA");im_rgba.alpha_composite(logo,(900-logo.width,1170-logo.height));im=im_rgba.convert("RGB")
        except Exception:pass
    out=io.BytesIO();im.save(out,format="PNG",optimize=True);out.seek(0);return out

def build_gemini_plus_prompt(title, body, area, audience, tone, style, palette_name, palette, visual_direction, visual_type="Cena editorial", density="Equilibrada", aspect_ratio="4:5"):
    bg,ink,accent=palette
    styles={
      "Fotografia editorial premium":"fotografia editorial realista, direção de arte de revista premium, luz controlada e natural, materiais e texturas verossímeis, enquadramento intencional",
      "Ilustração conceitual":"ilustração editorial autoral, metáfora visual específica, camadas e textura tátil, acabamento sofisticado de publicação de design",
      "Colagem contemporânea":"colagem editorial contemporânea, mistura refinada de fotografia e recortes geométricos, composição assimétrica, textura de papel e profundidade",
      "3D sofisticado":"natureza-morta 3D de alto padrão, objetos com materiais plausíveis, iluminação de estúdio e sombras delicadas",
      "Documental cinematográfica":"fotografia documental cinematográfica, ambiente realista, iluminação natural, detalhes observacionais e composição precisa",
      "Infográfico ilustrado":"infográfico editorial com poucos elementos, diagrama visual claro e ilustrações específicas, sem aparência de template pronto"
    }
    type_directions={
      "Cena editorial":"Uma única cena editorial forte: objeto, pessoa sem identidade reconhecível, ambiente e ação que expressem o ponto específico. Não tentar resumir a área inteira em símbolos genéricos.",
      "Conceito visual":"Uma metáfora visual original e concreta, ligada à ideia jurídica exata, sem clichês de advocacia.",
      "Infográfico sem texto":"Poucos elementos e hierarquia visual que expliquem a relação entre as ideias; não inserir palavras, números ou rótulos, pois serão aplicados depois.",
      "Fluxo de etapas":"Representar visualmente uma sequência de etapas com objetos distintos e setas/sinais visuais simples, sem texto ou números.",
      "Comparação de conceitos":"Dois contextos ou objetos visualmente distintos, em composição lado a lado, para mostrar a diferença sem incluir palavras.",
      "Linha do tempo visual":"Uma progressão cronológica representada por objetos ou marcos visuais, sem datas escritas.",
      "Checklist visual":"Um conjunto pequeno e organizado de documentos/objetos pertinentes ao tema, com espaços limpos para copy sobreposta.",
      "Colagem editorial":"Camadas assimétricas de fotografia e formas, textura tátil e recortes que traduzam o argumento do slide."
    }
    density_directions={
      "Enxuta":"Um assunto, um foco visual e bastante espaço negativo.",
      "Equilibrada":"Um foco dominante e no máximo dois elementos de apoio claramente subordinados.",
      "Rica em detalhes":"Mais textura, cenário e elementos contextuais, mantendo um foco dominante e sem poluição visual."
    }
    return f"""Crie uma imagem de fundo original e profissional para uma peça de comunicação jurídica brasileira. Proporção desejada {aspect_ratio}, formato vertical para Instagram e finalização posterior no AdvFlow.

CONTEÚDO EXATO
Headline: {title}
Ideia do slide: {str(body)[:700]}
Área jurídica: {area}
Público: {audience}
Tom: {tone}

DIREÇÃO DE ARTE
Tipo de composição: {visual_type}. {type_directions.get(visual_type, type_directions["Cena editorial"])}
Estilo: {styles.get(style,style)}.
Densidade: {density}. {density_directions.get(density,density_directions["Equilibrada"])}
Direção visual adicional: {visual_direction or "Interprete o argumento e escolha uma cena, objeto, gesto, material ou metáfora visual diretamente relacionados a esta pauta."}
Paleta: {palette_name}; harmonize with {bg} (background), {ink} (dark ink), and {accent} (accent colour), without forcing every colour into the scene.
Série: mantenha iluminação, tratamento, textura e paleta coerentes com os outros slides, mas mude o cenário e a metáfora de acordo com o argumento de cada slide.

COMPOSIÇÃO E ACABAMENTO
Acabamento de revista/editorial profissional; iluminação deliberada; profundidade e textura verossímeis; composição limpa e assimétrica, sem parecer uma imagem genérica de banco de imagens. Reserve espaço negativo limpo principalmente à esquerda e na parte inferior para que headline e copy sejam aplicadas depois. Posicione o assunto visual dominante à direita ou na região superior direita, sem cortar detalhes essenciais.

RESTRIÇÕES
Não incluir texto, letras, palavras, números, legendas, logotipos ou marcas-d'água. Não usar balança da justiça, martelo de juiz, pilha de livros jurídicos, aperto de mãos ou pessoas numa reunião de escritório, a menos que o assunto exija exatamente esse objeto ou contexto. Não mostrar documentos reais, nomes, números de processo ou dados de clientes. Não copiar a identidade visual de terceiros. Produza apenas a imagem de fundo, sem tipografia, em alta qualidade."""

def fetch_openrouter_image_models():
    """Fetch the live Image API catalog; returned model metadata controls optional parameters."""
    key=secret("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("Configure OPENROUTER_API_KEY em Streamlit Secrets para carregar o catálogo de imagens.")
    response=requests.get(
        "https://openrouter.ai/api/v1/images/models",
        headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"},
        timeout=25
    )
    if not response.ok:
        raise RuntimeError(f"Catálogo de imagens OpenRouter HTTP {response.status_code}: {error_detail(response)}")
    entries=response.json().get("data") or []
    models=[]
    for item in entries:
        if not isinstance(item,dict) or not isinstance(item.get("id"),str):
            continue
        architecture=item.get("architecture") or {}
        outputs=architecture.get("output_modalities") or []
        if outputs and "image" not in outputs:
            continue
        models.append({
            "id":item["id"],
            "name":str(item.get("name") or item["id"]),
            "description":str(item.get("description") or ""),
            "supported_parameters":item.get("supported_parameters") or [],
            "architecture":architecture,
            "endpoints":item.get("endpoints")
        })
    if not models:
        raise RuntimeError("O catálogo respondeu, mas não retornou modelos geradores de imagem.")
    return sorted(models,key=lambda row:(row["name"].lower(),row["id"].lower()))

def _supported_parameter_map(meta):
    value=(meta or {}).get("supported_parameters") or []
    if isinstance(value,dict):
        return value
    if isinstance(value,(list,tuple,set)):
        return {str(key):True for key in value}
    return {}

def _choose_enum_parameter(spec, preferred):
    """Choose a preferred supported enum value, or omit the parameter."""
    if isinstance(spec,dict):
        values=spec.get("values") or spec.get("enum") or spec.get("options")
        if isinstance(values,(list,tuple)) and values:
            for value in preferred:
                if value in values:
                    return value
            return values[0]
        return None
    if spec is True:
        return preferred[0] if preferred else None
    return None

def generate_openrouter_image(prompt, model_id, model_meta=None, reference_image=None, reference_mime="image/png"):
    """Generate one image via OpenRouter's unified Image API; this endpoint can consume credits."""
    key=secret("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY não configurada nos Secrets do Streamlit.")
    model_id=(model_id or "").strip()
    if not model_id or "/" not in model_id:
        raise RuntimeError("Selecione um identificador de modelo de imagem válido no catálogo OpenRouter.")
    params=_supported_parameter_map(model_meta or {})
    payload={"model":model_id,"prompt":prompt}
    # Pass optional controls only when the live catalog advertises them.
    if "aspect_ratio" in params:
        ratio=_choose_enum_parameter(params.get("aspect_ratio"),["4:5","3:4","2:3","9:16","1:1"])
        if ratio:
            payload["aspect_ratio"]=ratio
    else:
        # Known models in the reviewed Hermes provider metadata; only use ratios the provider advertises.
        known_ratios={
            "google/gemini-3.1-flash-image":["4:5","3:4","2:3","9:16","1:1"],
            "google/gemini-3.1-flash-lite-image":["4:5","3:4","2:3","9:16","1:1"],
            "qwen/qwen-image-3-pro":["4:5","3:4","2:3","9:16","1:1"],
            "openai/gpt-image-2":["3:4","2:3","9:16","1:1"],
            "openai/gpt-image-1-mini":["2:3","1:1"]
        }
        ratio_list=known_ratios.get(model_id)
        if ratio_list:
            payload["aspect_ratio"]=ratio_list[0]
    if "resolution" in params:
        resolution=_choose_enum_parameter(params.get("resolution"),["2K","1K","4K","512"])
        if resolution:
            payload["resolution"]=resolution
    if reference_image:
        if "input_references" not in params:
            raise RuntimeError("O modelo escolhido não anuncia suporte a imagem de referência. Escolha um modelo que liste input_references no catálogo ou retire a referência.")
        ref_url=f"data:{reference_mime};base64,{base64.b64encode(reference_image).decode('ascii')}"
        payload["input_references"]=[{"type":"image_url","image_url":{"url":ref_url}}]
    response=requests.post(
        "https://openrouter.ai/api/v1/images",
        headers={
            "Authorization":"Bearer "+key,
            "Content-Type":"application/json",
            "HTTP-Referer":"https://github.com/henriquetrader281-pixel/advflow",
            "X-Title":"AdvFlow"
        },
        json=payload,
        timeout=180
    )
    if not response.ok:
        detail=error_detail(response)
        if response.status_code==402:
            raise RuntimeError("OpenRouter informou saldo insuficiente ou requisito mínimo de saldo para geração. Confira o painel de créditos. Detalhe: "+detail)
        if response.status_code in (401,403):
            raise RuntimeError("Chave OpenRouter inválida ou sem autorização para este modelo. "+detail)
        if response.status_code==404:
            raise RuntimeError("Modelo não encontrado na Image API. Atualize o catálogo e escolha um modelo com saída de imagem. "+detail)
        raise RuntimeError(f"OpenRouter Image API HTTP {response.status_code}: {detail}")
    result=response.json()
    entries=result.get("data") or []
    if not entries or not isinstance(entries[0],dict) or not entries[0].get("b64_json"):
        raise RuntimeError("A API respondeu sem b64_json de imagem. Confira o modelo e tente outro.")
    try:
        image_bytes=base64.b64decode(entries[0]["b64_json"],validate=True)
        Image.open(io.BytesIO(image_bytes)).verify()
    except Exception as exc:
        raise RuntimeError("A resposta da Image API não continha uma imagem válida: "+str(exc)) from exc
    usage=result.get("usage") or {}
    return {
        "image_bytes":image_bytes,
        "media_type":str(entries[0].get("media_type") or "image/png"),
        "model":model_id,
        "cost":usage.get("cost"),
        "ratio":payload.get("aspect_ratio"),
        "resolution":payload.get("resolution"),
        "reference_used":bool(reference_image)
    }


def compose_uploaded_art(image_bytes, title, body, palette, signature="", area="Geral / interdisciplinar", slide_index=1, total_slides=1):
    if not str(title or "").strip() or not str(body or "").strip():
        raise ValueError("Título ou texto da arte vazio. Gere ou preencha o conteúdo antes de finalizar a imagem.")
    import imagem
    photo=ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    if photo.width < 200 or photo.height < 200:
        raise ValueError("A imagem é pequena demais para uma arte profissional. Envie uma imagem com pelo menos 200 × 200 pixels.")
    bg,ink,accent=PALETTES[palette]
    # Cover uses the photo-led editorial composition; following slides use a clean typography-led layout.
    focus=imagem.autofoco(photo) or (.5,.4)
    rendered=imagem.render(
        {"titulo":str(title).strip(),"texto":str(body).strip(),"kicker":str(area or "Conteúdo informativo")},
        max(0,int(slide_index)-1),max(1,int(total_slides)),1350,
        (bg,ink,accent),signature,"",photo if int(slide_index)==1 else None,
        None,focus,"Centro"
    )
    out=io.BytesIO();rendered.save(out,format="PNG",optimize=True);out.seek(0);return out



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
        if d.get("_modelo_utilizado"):
            st.caption("Modelo utilizado: "+str(d["_modelo_utilizado"]))
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
    st.subheader("Estúdio visual | Gemini Plus, OpenRouter e AdvFlow")
    st.caption("Método editorial: tipo de composição × estilo × paleta. Você pode usar a assinatura do Gemini manualmente ou gerar pela API do OpenRouter, com custo separado.")
    draft=st.session_state.get("draft",{})
    slides=draft.get("slides") or [{"titulo":draft.get("titulo","Tema jurídico"),"texto":draft.get("gancho","Defina a ideia central do conteúdo."),"visual":"Crie uma cena editorial específica para este assunto."}]
    options=[f"{i+1:02d} — {s.get('titulo','Slide')[:65]}" for i,s in enumerate(slides)]
    selected=st.selectbox("Slide que vamos ilustrar",options,index=0)
    slide_idx=options.index(selected); active=slides[slide_idx]
    title=st.text_input("Título da arte",value=active.get("titulo",draft.get("titulo","Tema jurídico")),key="studio_title")
    body=st.text_area("Texto que aparecerá na arte",value=active.get("texto",""),height=105,key="studio_body")
    visual_direction=st.text_area("Direção visual específica",value=active.get("visual",""),height=90,key="studio_direction",help="Detalhe objeto, ambiente, gesto, luz, enquadramento e metáfora visual. Evite apenas 'imagem jurídica'.")
    visual_type=st.selectbox("Tipo de composição",["Cena editorial","Conceito visual","Infográfico sem texto","Fluxo de etapas","Comparação de conceitos","Linha do tempo visual","Checklist visual","Colagem editorial"],index=0,help="Princípio adaptado do fluxo de ilustração do Hermes: definir tipo, estilo e paleta antes de gerar.")
    visual_style=st.selectbox("Estilo visual",["Fotografia editorial premium","Ilustração conceitual","Colagem contemporânea","3D sofisticado","Documental cinematográfica","Infográfico ilustrado"],index=0)
    visual_density=st.selectbox("Densidade visual",["Enxuta","Equilibrada","Rica em detalhes"],index=1)
    palette_options=list(PALETTES)
    chosen_palette=st.selectbox("Paleta para a arte",palette_options,index=palette_options.index(palette) if palette in palette_options else 0,key="studio_palette")
    if chosen_palette=="Personalizada":
        PALETTES["Personalizada"]=(st.session_state.get("custom_bg","#F5F1E8"),st.session_state.get("custom_ink","#172842"),st.session_state.get("custom_accent","#B79B61"))
    signature=office+(" • "+oab if oab else "")
    area=draft.get("area_direito","Geral / interdisciplinar")
    audience=draft.get("publico","público definido no briefing")
    tone=draft.get("tom_de_voz","sóbrio, didático e profissional")
    prompt=build_gemini_plus_prompt(title,body,area,audience,tone,visual_style,chosen_palette,PALETTES[chosen_palette],visual_direction,visual_type,visual_density)
    generation_mode=st.radio("Forma de gerar a imagem",["Gemini Plus manual — usa sua assinatura","OpenRouter — geração direta pela API"],index=0,horizontal=True,key="studio_generation_mode")
    if generation_mode=="OpenRouter — geração direta pela API":
        st.warning("A geração direta usa saldo/créditos do OpenRouter e pode cobrar por imagem. Não é coberta pela assinatura Gemini Plus. Só será feita ao clicar em “Gerar imagem agora”.")
        models_key="openrouter_image_models_cache"
        if st.button("Carregar/atualizar catálogo de modelos de imagem",key="refresh_openrouter_image_models"):
            try:
                with st.spinner("Consultando catálogo de imagens do OpenRouter…"):
                    st.session_state[models_key]=fetch_openrouter_image_models()
                st.success(f"Modelos de imagem disponíveis: {len(st.session_state[models_key])}.")
            except Exception as exc:
                st.error(str(exc))
        dynamic_models=st.session_state.get(models_key) or []
        fallback_models=[
            {"id":"google/gemini-3.1-flash-image","name":"Google Nano Banana 2 / Gemini 3.1 Flash Image","supported_parameters":{"aspect_ratio":{"values":["1:1","2:3","3:4","4:5","9:16","16:9"]},"resolution":{"values":["1K","2K","4K"]},"input_references":True}},
            {"id":"openai/gpt-image-2.5-sunburst","name":"OpenAI GPT Image 2.5 Sunburst","supported_parameters":{}},
            {"id":"bytedance-seed/seedream-5-0-flash","name":"ByteDance Seedream 5.0 Flash","supported_parameters":{}},
            {"id":"bytedance-seed/seedream-4.5","name":"ByteDance Seedream 4.5","supported_parameters":{"aspect_ratio":{"values":["1:1","4:5","3:4","2:3","9:16","16:9"]}}},
            {"id":"openai/gpt-image-2","name":"OpenAI GPT Image 2","supported_parameters":{"aspect_ratio":{"values":["1:1","3:2","2:3","4:3","3:4","16:9","9:16"]},"quality":{"values":["auto","low","medium","high"]}}},
            {"id":"qwen/qwen-image-3-pro","name":"Qwen Image 3 Pro","supported_parameters":{"aspect_ratio":{"values":["1:1","4:5","3:4","2:3","9:16","16:9"]},"resolution":{"values":["1K","2K"]},"input_references":True}}
        ]
        model_list=dynamic_models or fallback_models
        model_ids=[m["id"] for m in model_list]
        recommended=secret("OPENROUTER_IMAGE_MODEL","google/gemini-3.1-flash-image")
        default_model=recommended if recommended in model_ids else model_ids[0]
        chosen_model=st.selectbox("Modelo de imagem",model_ids,index=model_ids.index(default_model),format_func=lambda model_id: next((str(m.get("name") or model_id)+" · "+model_id for m in model_list if m["id"]==model_id),model_id),key="openrouter_image_model")
        model_metadata=next((m for m in model_list if m["id"]==chosen_model),{})
        custom_model=st.text_input("Outro identificador de modelo (opcional)",placeholder="Deixe vazio para usar o modelo selecionado")
        if custom_model.strip():
            chosen_model=custom_model.strip()
            model_metadata={}
        image_reference=st.file_uploader("Imagem de referência opcional (estilo/continuidade)",type=["png","jpg","jpeg","webp","jfif"],key="openrouter_image_reference",help="Só será enviada se o modelo anunciar suporte a input_references. Evite usar imagens com dados pessoais ou documentos reais.")
        confirm_cost=st.checkbox("Confirmo que quero consumir créditos do OpenRouter para gerar esta imagem.",key="confirm_openrouter_image_cost")
        if st.button("Gerar imagem agora",type="primary",key="generate_openrouter_image"):
            if not secret("OPENROUTER_API_KEY"):
                st.error("Configure OPENROUTER_API_KEY em Streamlit → Settings → Secrets.")
            elif not confirm_cost:
                st.warning("Marque a confirmação de custos antes de executar a geração.")
            else:
                reference_bytes=image_reference.getvalue() if image_reference else None
                reference_mime=(image_reference.type if image_reference else "image/png")
                try:
                    with st.spinner("Gerando imagem profissional com OpenRouter…"):
                        generated=generate_openrouter_image(prompt,chosen_model,model_metadata,reference_bytes,reference_mime)
                        final_art=compose_uploaded_art(generated["image_bytes"],title,body,chosen_palette,signature,area,slide_idx+1,len(slides))
                    st.session_state["openrouter_image_final"]=final_art.getvalue()
                    st.session_state["openrouter_image_meta"]=generated
                    st.session_state["openrouter_image_name"]=f"advflow_openrouter_slide_{slide_idx+1:02d}.png"
                    st.success("Imagem gerada e identidade visual aplicada.")
                except Exception as exc:
                    st.error("Não foi possível gerar a imagem: "+str(exc))
        if st.session_state.get("openrouter_image_final"):
            meta=st.session_state.get("openrouter_image_meta",{})
            st.image(st.session_state["openrouter_image_final"],use_container_width=True,caption="Arte gerada via OpenRouter e finalizada no AdvFlow")
            m1,m2,m3=st.columns(3)
            m1.caption("Modelo: "+str(meta.get("model","—")))
            m2.caption("Proporção enviada: "+str(meta.get("ratio") or "controlada pelo prompt/modelo"))
            cost=meta.get("cost")
            m3.caption("Custo informado pela API: "+(f"US$ {float(cost):.4f}" if isinstance(cost,(float,int)) else "não informado"))
            st.download_button("Baixar arte final PNG",st.session_state["openrouter_image_final"],st.session_state.get("openrouter_image_name","advflow_openrouter.png"),"image/png",key="download_openrouter_image_final")
    else:
        st.markdown("#### Etapa 1 — criar a imagem no Gemini Plus")
        st.caption("Abra o Gemini, cole o prompt abaixo, gere a imagem e salve o arquivo no computador. Peça a imagem sem texto: o AdvFlow aplica a tipografia depois e preserva melhor a acentuação.")
        st.text_area("Prompt pronto para usar",value=prompt,height=300,key="gemini_plus_prompt")
        st.download_button("Baixar prompt TXT",prompt.encode("utf-8"),"advflow_prompt_gemini.txt","text/plain",key="download_gemini_prompt")
        st.markdown("[Abrir o aplicativo Gemini](https://gemini.google.com/)")
        st.divider()
        st.markdown("#### Etapa 2 — importar e finalizar")
        uploaded=st.file_uploader("Envie a imagem gerada no Gemini Plus",type=["png","jpg","jpeg","webp","jfif"],key="gemini_plus_image_upload",help="Use imagens e referências que tenha direito de utilizar.")
        if uploaded:
            st.image(uploaded,use_container_width=True,caption="Imagem original gerada no Gemini")
            if st.button("Aplicar identidade visual e copy",type="primary",key="compose_gemini_plus_image"):
                try:
                    final_img=compose_uploaded_art(uploaded.getvalue(),title,body,chosen_palette,signature,area,slide_idx+1,len(slides))
                    st.session_state["gemini_plus_composed"]=final_img.getvalue()
                    st.session_state["gemini_plus_composed_name"]=f"advflow_slide_{slide_idx+1:02d}.png"
                    st.success("Imagem importada e finalizada.")
                except Exception as e:st.error("Não foi possível processar essa imagem: "+str(e))
        if st.session_state.get("gemini_plus_composed"):
            st.image(st.session_state["gemini_plus_composed"],use_container_width=True,caption="Arte final do AdvFlow")
            st.download_button("Baixar arte final PNG",st.session_state["gemini_plus_composed"],st.session_state.get("gemini_plus_composed_name","advflow_slide.png"),"image/png",key="download_composed_gemini_image")
    with st.expander("Alternativa sem IA"):
        st.caption("A arte local tem ilustração vetorial e tipografia, mas é menos rica que uma imagem editorial criada por IA.")
        if st.button("Gerar arte tipográfica local",key="make_local_studio_art"):
            buf=make_art(title,body,chosen_palette,signature,area,"Editorial premium",slide_idx+1,len(slides),draft.get("etapa_funil","Descoberta"))
            st.image(buf,use_container_width=True)
            st.download_button("Baixar arte local PNG",buf.getvalue(),"advflow_arte_local.png","image/png",key="download_local_studio_art")

with tabs[3]:
    st.subheader("Validador de métricas e funil editorial")
    st.caption("Método: escolha poucos indicadores relevantes, defina antes o nível que pede uma mudança e acompanhe por período, formato e etapa. Os dados são inseridos manualmente; o painel não está conectado ao Instagram/Meta.")
    with st.expander("Como interpretar este validador",expanded=False):
        st.write("As metas são definidas pelo próprio escritório e pelo histórico do perfil. O sistema não usa taxas universais como se fossem benchmarks confiáveis.")
        st.write("Alcance mede distribuição; interações, salvamentos e compartilhamentos medem tipos distintos de resposta; visitas ao perfil e cliques são sinais de próximo passo, não prova de contratação ou de causalidade.")
        st.write("O painel sinaliza qualidade de dados e se a meta definida foi atingida. Uma publicação isolada ou poucas peças não são suficientes para afirmar que um formato funciona melhor.")
        st.write("No marketing jurídico, métricas não justificam promessa de resultado, comparação, exposição de casos ou chamada que induza contratação. Revise o conteúdo sob as regras éticas vigentes.")
    METRIC_DEFS = [
        ("reach","Alcance médio por publicação","contas","Distribuição: média de contas alcançadas por peça."),
        ("engagement_rate","Interações / alcance","%","(Curtidas + comentários + salvamentos + compartilhamentos) / alcance total."),
        ("save_rate","Salvamentos / alcance","%","Sinal de utilidade percebida; não equivale a conversão."),
        ("share_rate","Compartilhamentos / alcance","%","Sinal de propagação; não é prova de intenção comercial."),
        ("profile_visit_rate","Visitas ao perfil / alcance","%","Sinal de interesse pelo perfil; comparar janelas compatíveis."),
        ("click_rate","Cliques em link / visitas ao perfil","%","Indicador-proxy. As fontes e as janelas dos dois dados precisam ser comparáveis.")
    ]
    # Targets are personalized, persistent per user, and intentionally have no universal default.
    with db() as c:
        target_rows=[dict(r) for r in c.execute("SELECT * FROM metric_targets WHERE user_id=?",(user_id,))]
    saved_targets={r["metric_key"]:r for r in target_rows}
    with st.expander("Definir metas e limites de ação",expanded=not bool(target_rows)):
        st.write("Para cada indicador, defina: **intervir abaixo de** (limite de alerta) e **meta desejada**. Use seu histórico, objetivo editorial e dados comparáveis. Deixe ambos em zero se ainda não tiver base para definir.")
        with st.form("metric_target_form"):
            proposed={}
            for key,label,unit,description in METRIC_DEFS:
                existing=saved_targets.get(key,{})
                col_a,col_b=st.columns(2)
                cap=(f" ({unit})" if unit=="%" else " (contas)")
                warning=col_a.number_input(f"{label} — intervir abaixo de{cap}",min_value=0.0,value=float(existing.get("warning_value",0.0)),step=(0.1 if unit=="%" else 10.0),key=f"target_warning_{key}")
                target=col_b.number_input(f"{label} — meta desejada{cap}",min_value=0.0,value=float(existing.get("target_value",0.0)),step=(0.1 if unit=="%" else 10.0),key=f"target_goal_{key}")
                st.caption(description)
                proposed[key]=(warning,target,unit,label)
            save_targets=st.form_submit_button("Salvar metas do meu perfil",type="primary")
        if save_targets:
            invalid=[]
            for key,(warning,target,unit,label) in proposed.items():
                if (warning>0 or target>0) and not (target>warning>0):
                    invalid.append(f"{label}: a meta precisa ser maior que o limite de ação, e ambos devem ser maiores que zero.")
            if invalid:
                for msg in invalid: st.error(msg)
            else:
                with db() as c:
                    for key,(warning,target,unit,label) in proposed.items():
                        if target>0 and warning>0:
                            c.execute("INSERT INTO metric_targets(user_id,metric_key,target_value,warning_value,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,metric_key) DO UPDATE SET target_value=excluded.target_value,warning_value=excluded.warning_value,updated_at=excluded.updated_at",
                                      (user_id,key,target,warning,datetime.now().isoformat(timespec="seconds")))
                        else:
                            c.execute("DELETE FROM metric_targets WHERE user_id=? AND metric_key=?",(user_id,key))
                st.success("Metas salvas.")
                st.rerun()
    with st.form("metric_form"):
        mdate=st.date_input("Data da publicação",value=date.today())
        topic=st.text_input("Tema / título")
        mformat=st.selectbox("Formato",["Carrossel","Reel","Post estático","Story","Outro"])
        stage=st.selectbox("Etapa editorial",["Descoberta","Consideração","Relacionamento","Conversão editorial"])
        objective=st.selectbox("Objetivo principal da peça",["Alcance qualificado","Interações úteis","Salvamentos","Compartilhamentos","Visitas ao perfil","Cliques em link"])
        cols=st.columns(4)
        reach=cols[0].number_input("Alcance",min_value=0,step=1)
        likes=cols[1].number_input("Curtidas",min_value=0,step=1)
        comments=cols[2].number_input("Comentários",min_value=0,step=1)
        saves=cols[3].number_input("Salvamentos",min_value=0,step=1)
        cols2=st.columns(4)
        shares=cols2[0].number_input("Compartilhamentos",min_value=0,step=1)
        profile_visits=cols2[1].number_input("Visitas ao perfil atribuídas",min_value=0,step=1)
        link_clicks=cols2[2].number_input("Cliques em links",min_value=0,step=1)
        completeness=cols2[3].selectbox("Cobertura dos Insights",["Parcial","Completa"],help="Marque Completa apenas quando registrou os campos relevantes disponíveis para esta peça.")
        save_m=st.form_submit_button("Registrar publicação e métricas")
    if save_m:
        if not topic.strip():
            st.warning("Informe o tema/título para que as comparações por pauta façam sentido.")
        else:
            with db() as c:
                c.execute("INSERT INTO metrics(post_date,topic,format,reach,likes,comments,saves,shares,user_id,funnel_stage,profile_visits,link_clicks,data_completeness,objective) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (str(mdate),topic.strip(),mformat,reach,likes,comments,saves,shares,user_id,stage,profile_visits,link_clicks,completeness,objective))
            st.success("Métricas registradas.")
    with db() as c:
        rows=[dict(r) for r in c.execute("SELECT * FROM metrics WHERE user_id=? ORDER BY post_date DESC",(user_id,))]
    if rows:
        df=pd.DataFrame(rows)
        for col,default in [("profile_visits",0),("link_clicks",0),("funnel_stage","Descoberta"),("data_completeness","Parcial"),("objective","Interações úteis")]:
            if col not in df.columns: df[col]=default
        df["date_parsed"]=pd.to_datetime(df["post_date"],errors="coerce")
        df["stage_key"]=df["funnel_stage"].fillna("Descoberta").astype(str).str.split("|").str[0].str.strip()
        # Data-quality checks are informational; the app does not silently rewrite historic inputs.
        bad_dates=int(df["date_parsed"].isna().sum())
        no_reach=int((df["reach"].fillna(0)<=0).sum())
        blank_topics=int(df["topic"].fillna("").astype(str).str.strip().eq("").sum())
        partial_rows=int(df["data_completeness"].fillna("Parcial").ne("Completa").sum())
        dupes=int(df.duplicated(subset=["post_date","topic","format"],keep=False).sum())
        with st.expander("Validação da qualidade dos dados",expanded=(bad_dates+no_reach+blank_topics+partial_rows>0)):
            q1,q2,q3,q4=st.columns(4)
            q1.metric("Datas inválidas",bad_dates)
            q2.metric("Sem alcance válido",no_reach)
            q3.metric("Tema vazio",blank_topics)
            q4.metric("Dados parciais",partial_rows)
            if dupes:
                st.warning(f"{dupes} linhas participam de possíveis duplicatas de data + tema + formato. Confira se alguma publicação foi registrada duas vezes; nenhuma linha foi removida.")
            if no_reach:
                st.warning("As taxas com denominador alcance ficam indisponíveis nas linhas com alcance zero. Confirme se o zero significa ausência real de alcance ou dado não coletado.")
            if partial_rows:
                st.info("Os registros parciais entram nas contagens brutas, mas recomenda-se não usá-los para concluir sobre cliques, visitas ou a taxa de funil. Marque a cobertura real ao registrar cada publicação.")
            if bad_dates or blank_topics:
                st.warning("Corrija datas ou temas vazios na fonte dos dados antes de comparar pautas. As entradas antigas são sinalizadas, não alteradas automaticamente.")
            if not (bad_dates or no_reach or blank_topics or partial_rows or dupes):
                st.success("Não foram encontrados alertas básicos de integridade nos registros exibidos. Isso não garante que as métricas de origem estejam corretas.")
        period_label=st.selectbox("Janela para validação",["Últimos 7 dias","Últimos 30 dias","Últimos 90 dias","Últimos 180 dias","Todo o histórico"],index=0)
        period_days={"Últimos 7 dias":7,"Últimos 30 dias":30,"Últimos 90 dias":90,"Últimos 180 dias":180,"Todo o histórico":None}[period_label]
        date_now=pd.Timestamp(date.today())
        stage_filter=st.selectbox("Filtrar etapa",["Todas","Descoberta","Consideração","Relacionamento","Conversão editorial"])
        format_filter=st.selectbox("Filtrar formato",["Todos"]+sorted([str(x) for x in df["format"].dropna().unique()]))
        recent=df.copy()
        if period_days is not None:
            cutoff=date_now-pd.Timedelta(days=period_days-1)
            recent=recent[recent["date_parsed"].between(cutoff,date_now,inclusive="both")]
        if stage_filter!="Todas": recent=recent[recent["stage_key"]==stage_filter]
        if format_filter!="Todos": recent=recent[recent["format"]==format_filter]
        def metric_values(frame):
            reach_sum=float(frame["reach"].fillna(0).sum()) if len(frame) else 0.0
            interactions=float(frame[["likes","comments","saves","shares"]].fillna(0).sum().sum()) if len(frame) else 0.0
            saves_sum=float(frame["saves"].fillna(0).sum()) if len(frame) else 0.0
            shares_sum=float(frame["shares"].fillna(0).sum()) if len(frame) else 0.0
            visits_sum=float(frame["profile_visits"].fillna(0).sum()) if len(frame) else 0.0
            clicks_sum=float(frame["link_clicks"].fillna(0).sum()) if len(frame) else 0.0
            n=max(int(len(frame)),1)
            return {
                "reach":float(frame["reach"].fillna(0).mean()) if len(frame) else 0.0,
                "engagement_rate":(interactions/reach_sum*100) if reach_sum else None,
                "save_rate":(saves_sum/reach_sum*100) if reach_sum else None,
                "share_rate":(shares_sum/reach_sum*100) if reach_sum else None,
                "profile_visit_rate":(visits_sum/reach_sum*100) if reach_sum else None,
                "click_rate":(clicks_sum/visits_sum*100) if visits_sum else None,
                "posts":int(len(frame)),
                "reach_total":reach_sum,
                "interactions_total":interactions,
                "visits_total":visits_sum,
                "clicks_total":clicks_sum
            }
        now_metrics=metric_values(recent)
        st.markdown("#### Resultado no período")
        st.caption(f"{len(recent)} publicações no filtro selecionado. Taxas agregadas são calculadas por totais (não por média simples de percentuais), para que o alcance pese no denominador.")
        if len(recent)==0:
            st.info("Nenhuma publicação neste recorte. Registre publicações ou amplie o período.")
        else:
            cards=[
                ("reach","Alcance médio / publicação","contas"),
                ("engagement_rate","Interações / alcance","%"),
                ("save_rate","Salvamentos / alcance","%"),
                ("share_rate","Compartilhamentos / alcance","%"),
                ("profile_visit_rate","Visitas ao perfil / alcance","%"),
                ("click_rate","Cliques / visitas ao perfil*","%")
            ]
            metric_actions={
                "reach":"Teste uma alteração por vez no gancho, no assunto ou na distribuição. Compare peças do mesmo tipo e da mesma etapa antes de concluir.",
                "engagement_rate":"Leia separadamente comentários, salvamentos e compartilhamentos. Se o conteúdo recebe poucas interações, teste clareza, especificidade e aderência ao público — não apenas perguntas artificiais.",
                "save_rate":"Teste um recurso realmente consultável: checklist, distinção conceitual, sequência de documentos ou referência oficial. Evite transformar hipótese em regra absoluta.",
                "share_rate":"Destaque uma explicação útil para um grupo claramente definido e um resumo fácil de repassar; evite alarmismo ou promessa de resultado.",
                "profile_visit_rate":"Verifique se o tema, a identificação profissional e a organização do perfil se conectam. Mantenha a comunicação informativa, sem induzir contratação.",
                "click_rate":"Confira se o link funciona, se o destino corresponde à pauta e se a origem/janela das métricas é comparável. Priorize material informativo ou fonte oficial."
            }
            card_cols=st.columns(3)
            for i,(key,label,unit) in enumerate(cards):
                value=now_metrics.get(key)
                target=saved_targets.get(key)
                if value is None:
                    display="n/d"; status="Sem denominador suficiente"
                else:
                    display=(f"{value:,.1f}".replace(",","X").replace(".",",").replace("X",".")+" contas") if unit=="contas" else f"{value:.2f}%"
                    if not target:
                        status="Defina meta e limite para validar"
                    elif value < float(target["warning_value"]):
                        status="Abaixo do limite de ação"
                    elif value >= float(target["target_value"]):
                        status="Meta atingida"
                    else:
                        status="Monitorar: entre limite e meta"
                with card_cols[i%3]:
                    st.metric(label,display)
                    if value is not None and key in saved_targets:
                        delta_text=f"Alvo: {saved_targets[key]['target_value']:g} {unit} · intervir abaixo de {saved_targets[key]['warning_value']:g}"
                        st.caption(delta_text)
                    st.caption(status)
                    if value is not None and target and value < float(target["warning_value"]):
                        st.caption("**Próxima hipótese a testar:** "+metric_actions.get(key,"Revise o objetivo e a qualidade da coleta antes de agir."))
            if len(recent)<5:
                st.warning("Amostra pequena (menos de 5 publicações neste recorte). Trate a leitura como exploratória e não declare um formato vencedor.")
            if recent["data_completeness"].fillna("Parcial").ne("Completa").any():
                st.warning("Há registros parciais no período. Taxas de perfil/cliques podem estar subestimadas se os campos não foram coletados.")
        # Side-by-side trend against an equally long earlier period, using the same stage/format filters.
        if period_days is not None:
            prev_cutoff=cutoff-pd.Timedelta(days=period_days)
            previous=df[df["date_parsed"].between(prev_cutoff,cutoff-pd.Timedelta(days=1),inclusive="both")].copy()
            if stage_filter!="Todas": previous=previous[previous["stage_key"]==stage_filter]
            if format_filter!="Todos": previous=previous[previous["format"]==format_filter]
            if len(previous) and len(recent):
                prev_metrics=metric_values(previous)
                st.markdown("#### Comparação com a janela anterior")
                st.caption(f"Atual: {len(recent)} publicações · anterior: {len(previous)}. Mudança é descritiva, não prova que uma alteração de conteúdo causou o resultado.")
                trend_rows=[]
                for key,label,unit in cards:
                    current_v=now_metrics.get(key); previous_v=prev_metrics.get(key)
                    if current_v is None or previous_v is None:
                        change="n/d"
                    elif unit=="%":
                        change=f"{current_v-previous_v:+.2f} p.p."
                    else:
                        change=f"{current_v-previous_v:+.1f} contas"
                    trend_rows.append({"Indicador":label,"Atual":current_v,"Janela anterior":previous_v,"Variação":change})
                st.dataframe(pd.DataFrame(trend_rows),use_container_width=True,hide_index=True)
        # Compare formats/stages by computing a ratio of totals for each group.
        st.markdown("#### Comparação por formato")
        format_rows=[]
        for group_name,group in recent.groupby("format",dropna=False):
            vals=metric_values(group)
            format_rows.append({"Formato":str(group_name),"Publicações":vals["posts"],"Alcance médio":round(vals["reach"],1),"Interações/alcance %":round(vals["engagement_rate"],2) if vals["engagement_rate"] is not None else None,"Salvamentos/alcance %":round(vals["save_rate"],2) if vals["save_rate"] is not None else None,"Compartilhamentos/alcance %":round(vals["share_rate"],2) if vals["share_rate"] is not None else None})
        if format_rows:
            st.dataframe(pd.DataFrame(format_rows).sort_values("Publicações",ascending=False),use_container_width=True,hide_index=True)
        st.markdown("#### Comparação por etapa do funil")
        stage_rows=[]
        for group_name,group in recent.groupby("stage_key",dropna=False):
            vals=metric_values(group)
            stage_rows.append({"Etapa":str(group_name),"Publicações":vals["posts"],"Alcance médio":round(vals["reach"],1),"Interações/alcance %":round(vals["engagement_rate"],2) if vals["engagement_rate"] is not None else None,"Salvamentos/alcance %":round(vals["save_rate"],2) if vals["save_rate"] is not None else None,"Visitas/alcance %":round(vals["profile_visit_rate"],2) if vals["profile_visit_rate"] is not None else None})
        if stage_rows:
            st.dataframe(pd.DataFrame(stage_rows).sort_values("Publicações",ascending=False),use_container_width=True,hide_index=True)
        st.markdown("#### Comparação por objetivo informado")
        objective_rows=[]
        for group_name,group in recent.groupby("objective",dropna=False):
            vals=metric_values(group)
            objective_rows.append({"Objetivo":str(group_name),"Publicações":vals["posts"],"Alcance médio":round(vals["reach"],1),"Interações/alcance %":round(vals["engagement_rate"],2) if vals["engagement_rate"] is not None else None,"Salvamentos/alcance %":round(vals["save_rate"],2) if vals["save_rate"] is not None else None,"Compartilhamentos/alcance %":round(vals["share_rate"],2) if vals["share_rate"] is not None else None,"Visitas/alcance %":round(vals["profile_visit_rate"],2) if vals["profile_visit_rate"] is not None else None})
        if objective_rows:
            st.dataframe(pd.DataFrame(objective_rows).sort_values("Publicações",ascending=False),use_container_width=True,hide_index=True)
        st.caption("Leitura semanal recomendada: escolha 2–3 indicadores que correspondam ao objetivo da publicação e defina de antemão qual valor acionará uma revisão. Não tente otimizar todos os números ao mesmo tempo.")
        show_all=st.checkbox("Ver tabela completa de publicações",value=False)
        if show_all:
            display_df=df.drop(columns=["date_parsed"],errors="ignore").copy()
            st.dataframe(display_df,use_container_width=True,hide_index=True)
        st.download_button("Exportar registros CSV",df.drop(columns=["date_parsed"],errors="ignore").to_csv(index=False).encode("utf-8-sig"),"advflow_metricas.csv","text/csv")
    else:
        st.info("Ainda não há métricas. Comece registrando publicações e os dados disponíveis nos Insights.")
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
    st.markdown("- **Alternativa Groq:** configure GROQ_API_KEY; o modelo padrão vem de GROQ_MODEL.")
    st.markdown("- **OpenRouter:** configure OPENROUTER_API_KEY em Secrets. O modelo padrão é openrouter/free; você pode alterá-lo em OPENROUTER_MODEL. Modelos gratuitos têm cotas e limites próprios.")
    st.markdown("- **Chaves de API:** nunca publique chaves no GitHub; mantenha-as nos Secrets do Streamlit.")
    if st.button("Testar conexão OpenRouter",key="test_openrouter_connection"):
        try:
            with st.spinner("Testando OpenRouter…"):
                tested_model=test_openrouter_connection()
            st.success("OpenRouter respondeu com sucesso. Modelo: "+tested_model)
        except Exception as exc:
            st.error("Falha no teste OpenRouter: "+str(exc))
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
