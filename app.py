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

def persistent_auth_enabled():
    """True when Supabase auth settings were supplied; partial setup fails closed instead of using local SQLite."""
    return bool(
        secret("SUPABASE_URL").strip()
        or secret("SUPABASE_SECRET_KEY").strip()
        or secret("SUPABASE_SERVICE_ROLE_KEY").strip()
    )

def supabase_auth_key():
    return secret("SUPABASE_SECRET_KEY").strip() or secret("SUPABASE_SERVICE_ROLE_KEY").strip()

def supabase_auth_request(method, params=None, payload=None, prefer=None):
    """Access only the auth table through Supabase REST from the server."""
    base_url = secret("SUPABASE_URL").strip().rstrip("/")
    key = supabase_auth_key()
    if not base_url or not key:
        raise RuntimeError("Configure SUPABASE_URL and SUPABASE_SECRET_KEY in hosting Secrets.")
    headers = {
        "apikey": key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    # New sb_secret_* API keys are not JWTs and belong in apikey only.
    # Legacy service_role keys are JWTs and need the Authorization header.
    if key.startswith("eyJ"):
        headers["Authorization"] = "Bearer " + key
    if prefer:
        headers["Prefer"] = prefer
    try:
        response = requests.request(
            method.upper(), base_url + "/rest/v1/advflow_users",
            headers=headers, params=params or {}, json=payload, timeout=15
        )
    except requests.RequestException as exc:
        raise RuntimeError("Não foi possível alcançar o banco persistente de login (" + exc.__class__.__name__ + ").") from exc
    if not response.ok:
        try:
            data = response.json()
            detail = str(data.get("message") or data.get("hint") or data.get("details") or data.get("code") or data)[:350] if isinstance(data, dict) else str(data)[:350]
        except Exception:
            detail = (response.text or "sem detalhes")[:350]
        if response.status_code == 404:
            raise RuntimeError("A tabela public.advflow_users não foi encontrada. Execute supabase/schema.sql no SQL Editor do Supabase.")
        if response.status_code in (401, 403):
            raise RuntimeError("Supabase recusou a chave ou a permissão do banco. Confira SUPABASE_URL e a chave secreta nos Secrets.")
        raise RuntimeError(f"Banco persistente de login respondeu HTTP {response.status_code}: {detail}")
    if response.status_code == 204 or not response.content:
        return []
    data = response.json()
    return data if isinstance(data, list) else [data]

def local_auth_users_list():
    with db() as c:
        rows = c.execute(
            "SELECT username,password_salt,password_hash,role,active,must_change_password,failed_attempts,locked_until,created_at FROM users ORDER BY username"
        ).fetchall()
    return [dict(row) for row in rows]

def persistent_auth_users_list(migrate_local_once=True):
    """Load persistent users; if empty, migrate existing local password hashes one time."""
    rows = supabase_auth_request("GET", params={"select": "*", "order": "username.asc"})
    if not rows and migrate_local_once:
        local_rows = local_auth_users_list()
        if local_rows:
            for row in local_rows:
                row["active"] = bool(row.get("active", 1))
                row["must_change_password"] = bool(row.get("must_change_password", 0))
                supabase_auth_request("POST", payload=row, prefer="return=minimal")
            rows = supabase_auth_request("GET", params={"select": "*", "order": "username.asc"})
    return rows

def auth_users_list():
    return persistent_auth_users_list() if persistent_auth_enabled() else local_auth_users_list()

def auth_user_get(username):
    clean = (username or "").strip().lower()
    if persistent_auth_enabled():
        rows = supabase_auth_request("GET", params={"select": "*", "username": "eq." + clean, "limit": "1"})
        return rows[0] if rows else None
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (clean,)).fetchone()
    return dict(row) if row else None

def auth_user_insert(username, salt, digest, role="user", active=True, must_change_password=False, created_at=None):
    record = {
        "username": username, "password_salt": salt, "password_hash": digest,
        "role": role, "active": bool(active), "must_change_password": bool(must_change_password),
        "failed_attempts": 0, "locked_until": None,
        "created_at": created_at or datetime.now().isoformat(timespec="seconds"),
    }
    if persistent_auth_enabled():
        supabase_auth_request("POST", payload=record, prefer="return=minimal")
        return record
    with db() as c:
        c.execute(
            "INSERT INTO users(username,password_salt,password_hash,role,active,must_change_password,failed_attempts,locked_until,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (record["username"],record["password_salt"],record["password_hash"],record["role"],int(record["active"]),int(record["must_change_password"]),0,None,record["created_at"])
        )
    return record

def auth_user_update(username, fields):
    clean = (username or "").strip().lower()
    if persistent_auth_enabled():
        return supabase_auth_request("PATCH", params={"username": "eq." + clean}, payload=fields, prefer="return=representation")
    allowed = {"password_salt","password_hash","role","active","must_change_password","failed_attempts","locked_until"}
    updates = {k:v for k,v in fields.items() if k in allowed}
    if not updates:
        return []
    clause = ", ".join(f"{key}=?" for key in updates)
    values = [int(v) if key in ("active","must_change_password") and isinstance(v,bool) else v for key,v in updates.items()]
    with db() as c:
        c.execute(f"UPDATE users SET {clause} WHERE username=?", (*values,clean))
    return []

def persistent_auth_warning():
    if not persistent_auth_enabled():
        st.warning(
            "O login ainda usa SQLite local e pode ser perdido quando a hospedagem reinicia ou publica uma nova versão. "
            "Configure SUPABASE_URL e SUPABASE_SECRET_KEY e crie a tabela conforme supabase/schema.sql para manter as contas."
        )

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
    remote_store = persistent_auth_enabled()
    try:
        if remote_store:
            accounts = persistent_auth_users_list()
            account_count = len(accounts)
        else:
            with db() as c:
                account_count = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        username = st.session_state.get("auth_user")
        account = auth_user_get(username) if username else None
        if username and (account is None or not bool(account.get("active", 0))):
            st.session_state.pop("auth_user", None)
            username = None
            account = None
    except Exception as exc:
        st.error("Não foi possível consultar o banco de contas. O login foi interrompido para evitar criar uma conta local divergente.")
        st.error(str(exc))
        st.info("Confira SUPABASE_URL e SUPABASE_SECRET_KEY e execute supabase/schema.sql no SQL Editor do Supabase.")
        st.stop()

    if not remote_store:
        persistent_auth_warning()

    if account_count == 0:
        st.info("Primeiro acesso: configure uma chave privada de instalação e use-a somente para criar o primeiro administrador.")
        setup_key_expected=secret("INITIAL_SETUP_KEY")
        if not setup_key_expected:
            st.error("Configuração pendente: adicione INITIAL_SETUP_KEY em Streamlit Secrets antes de liberar o cadastro inicial. Gere uma chave longa e aleatória e não use o valor de exemplo.")
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
                try:
                    auth_user_insert(clean_user,salt,digest,"admin",True,False,datetime.now().isoformat(timespec="seconds"))
                    for table_name in ("pieces","metrics","calendar"):
                        with db() as c:
                            c.execute(f"UPDATE {table_name} SET user_id=? WHERE user_id='legacy'",(clean_user,))
                    st.session_state["auth_user"] = clean_user
                    st.rerun()
                except Exception as exc:
                    st.error("Não foi possível criar a conta de administrador: " + str(exc))
        st.stop()

    if account is None:
        with st.form("login_form"):
            login_user = st.text_input("Usuário")
            login_password = st.text_input("Senha", type="password")
            login_submit = st.form_submit_button("Entrar", type="primary")
        if login_submit:
            clean_user = (login_user or "").strip().lower()
            try:
                record = auth_user_get(clean_user)
                now = datetime.now()
                if record and bool(record.get("active", 0)):
                    locked_until = record.get("locked_until")
                    if locked_until:
                        lock_dt = datetime.fromisoformat(str(locked_until).replace("Z", "+00:00"))
                        clock = datetime.now(lock_dt.tzinfo) if lock_dt.tzinfo else now
                        if lock_dt > clock:
                            st.error("Conta temporariamente bloqueada após falhas de autenticação. Aguarde o fim do bloqueio de 15 minutos.")
                            st.stop()
                    if password_matches(login_password,record["password_salt"],record["password_hash"]):
                        auth_user_update(clean_user, {"failed_attempts":0,"locked_until":None})
                        st.session_state["auth_user"] = record["username"]
                        st.rerun()
                    failures = int(record.get("failed_attempts") or 0) + 1
                    lock_time = (now + timedelta(minutes=15)).isoformat(timespec="seconds") if failures >= 5 else None
                    auth_user_update(clean_user, {"failed_attempts":failures,"locked_until":lock_time})
                st.error("Usuário ou senha inválidos. Cinco tentativas incorretas bloqueiam o acesso por 15 minutos.")
            except Exception as exc:
                st.error("Não foi possível validar o login no banco de contas: " + str(exc))
        st.caption("Esqueceu a senha? Solicite ao administrador uma redefinição. Esta versão não envia e-mails de recuperação.")
        st.stop()

    if bool(account.get("must_change_password",0)):
        st.warning("Por segurança, altere a senha temporária para continuar.")
        with st.form("required_password_change"):
            pw1 = st.text_input("Nova senha",type="password")
            pw2 = st.text_input("Confirme a nova senha",type="password")
            submit_change = st.form_submit_button("Salvar nova senha",type="primary")
        if submit_change:
            if len(pw1) < 12:
                st.error("A senha deve ter pelo menos 12 caracteres.")
            elif pw1 != pw2:
                st.error("As senhas não conferem.")
            else:
                salt,digest=password_record(pw1)
                try:
                    auth_user_update(account["username"], {
                        "password_salt":salt, "password_hash":digest,
                        "must_change_password":False, "failed_attempts":0, "locked_until":None,
                    })
                    st.success("Senha atualizada.")
                    st.rerun()
                except Exception as exc:
                    st.error("Não foi possível salvar a nova senha no banco persistente: " + str(exc))
        st.stop()
    return dict(account)

current_user = auth_gate()
user_id = current_user["username"]

SYSTEM = """Você é diretor de criação e estrategista sênior de conteúdo para advocacia brasileira. Produza copy editorial de nível profissional: específica, fluida, persuasiva pelo valor informativo (nunca por pressão comercial), com raciocínio progressivo e linguagem acessível para o público solicitado. Evite clichês, introduções vazias, repetição entre slides, frases genéricas do tipo 'é importante conhecer', perguntas artificiais, promessas de resultado, sensacionalismo, comparação, autoengrandecimento, captação de clientela e estímulo ao litígio. Não invente leis, artigos, prazos, estatísticas, decisões, fatos ou fontes. Quando o contexto estiver incompleto, use formulação condicional e indique o que precisa ser confirmado. O texto deve conter uma ideia concreta por slide, título editorial curto, parágrafo objetivo e transição lógica até a conclusão. Adeque profundidade, vocabulário, exemplos e objeções à área e ao segmento escolhidos. Diferencie informação geral de aconselhamento individual. Respeite a publicidade informativa, sóbria e discreta exigida pela OAB; não use CTA que induza contratação. A etapa de funil define intenção editorial, não uma promessa de conversão comercial.

REVISÃO OBRIGATÓRIA ANTES DE ENTREGAR:
- Revise todo o conteúdo em português brasileiro: ortografia, acentuação, concordância, regência, pontuação, sintaxe, coesão e naturalidade.
- Revise a terminologia jurídica: use o termo técnico correto para o instituto descrito; não trate conceitos diferentes como sinônimos nem substitua termos jurídicos por palavras vagas.
- Escreva para leigos inteligentes: prefira palavras comuns, frases diretas e exemplos cotidianos, sem perder rigor jurídico.
- Ao sugerir o visual de cada slide, prefira uma situação concreta, objeto ou cena ligada ao tema. Não proponha quebra-cabeças com palavras jurídicas soltas como metáfora genérica nem infográficos que exijam que o leitor já conheça os termos.
- Não use rótulos internos de roteiro como título visível. Nunca deixe títulos como “Gancho”, “Introdução”, “Contexto”, “Explicação”, “Conclusão” ou “Slide 1” como manchete final; transforme-os em títulos editoriais específicos para o tema e para o texto daquele slide.
- Não coloque termos técnicos isolados sem explicação, como “ato ilícito”, “nexo causal” ou “dano”. Quando necessários, explique brevemente em português simples na própria peça (por exemplo, “conduta contrária ao direito”, “ligação entre a conduta e o prejuízo” e “prejuízo sofrido”), sem tratar essas expressões como definição universal quando o contexto exigir nuance.
- Distinga fato, regra geral, exceção, interpretação e hipótese. Não transforme uma possibilidade em certeza nem apresente orientação geral como conclusão aplicável a todo caso.
- Não invente leis, artigos, prazos, súmulas, precedentes, requisitos ou fontes. Quando a precisão jurídica depender de confirmação em fonte primária atualizada, sinalize isso claramente em fontes_para_conferir e checklist_revisao.
- Não reescreva de forma que mude o sentido jurídico. Se houver ambiguidade ou informação insuficiente, sinalize o trecho em vez de adivinhar.
- Confira também a consistência dos termos entre slides, roteiro, legenda, CTA e direção visual. A revisão linguística não equivale à validação de vigência legal ou a um parecer jurídico.

Retorne JSON válido com as chaves:
titulo, promessa_editorial, angulo_editorial, publico, area_direito, etapa_funil, resumo_estrategico, gancho, slides (lista de objetos titulo/texto/visual/objetivo_do_slide), roteiro (lista de cena/fala/visual), legenda, cta_educativa, hashtags, fontes_para_conferir, checklist_revisao.
No checklist_revisao, inclua itens sobre português brasileiro, precisão e consistência terminológica jurídica, clareza para o público e fundamentos que precisam de conferência em fonte primária.
Carrossel: 6 a 8 slides e progressão gancho-contexto-explicação-aplicação-cuidados-síntese. Reel: roteiro para 30–45 segundos, cenas com fala natural, indicação de texto na tela e plano visual. Post estático: uma única ideia clara. Legenda: texto completo, sem slides. Artigo curto: título específico, subtítulos úteis, desenvolvimento objetivo, distinção entre regra geral e exceções e conclusão informativa, sem inventar fundamentos. O CTA deve corresponder ao funil e ser educativo ou institucional (por exemplo, salvar, compartilhar informação, consultar fonte oficial, acompanhar conteúdos ou conhecer informações institucionais); nunca pressionar a contratação. Não use emojis nem símbolos decorativos no texto final. Evite escassez, urgência artificial, “você não vai acreditar”, “segredo”, “imperdível”, “EU QUERO” e outros recursos de pressão. A abertura pode gerar identificação, mas não sensacionalismo. Use no máximo três hashtags, apenas quando forem úteis.""" 

LINGUISTIC_REVIEW_SYSTEM = """Você é revisor sênior de português brasileiro e terminologia jurídica brasileira, com rigor editorial. Analise a peça e apresente uma revisão cuidadosa, sem inventar base legal nem se passar por parecer jurídico.
Verifique ortografia, acentuação, gramática, concordância, regência, pontuação, sintaxe, naturalidade, clareza, coesão e consistência de termos entre título, slides, roteiro e legenda.
Confira se cada termo jurídico está empregado no sentido correto e se há confusão entre conceitos distintos. Evite juridiquês gratuito, mas não simplifique a ponto de alterar o instituto jurídico. Explique em linguagem acessível os termos técnicos inevitáveis.
Não declare que uma lei, artigo, prazo ou precedente está vigente/correto sem fonte confiável incluída no material. Separe falhas linguísticas de questões jurídicas que precisam ser verificadas. Não altere fatos, requisitos, direitos, obrigações, prazos, números ou referências sem suporte. Caso exista dúvida de mérito, preserve o sentido, sinalize o trecho e indique a fonte primária que deve ser consultada.
Retorne apenas JSON válido neste formato:
{
  "resumo_da_revisao": "síntese breve",
  "problemas_identificados": [
    {"trecho_original":"trecho literal ou descrição objetiva","tipo":"Português|Terminologia jurídica|Clareza|Consistência|Verificação jurídica","motivo":"por que precisa de ajuste","sugestao":"proposta específica"}
  ],
  "pontos_que_exigem_fonte": ["questões jurídicas factuais que não podem ser confirmadas apenas pela redação"],
  "versao_revisada": {
    "titulo":"...",
    "promessa_editorial":"...",
    "angulo_editorial":"...",
    "publico":"...",
    "area_direito":"...",
    "etapa_funil":"...",
    "resumo_estrategico":"...",
    "gancho":"...",
    "slides":[{"titulo":"...","texto":"...","visual":"...","objetivo_do_slide":"..."}],
    "roteiro":[{"cena":"...","fala":"...","visual":"..."}],
    "legenda":"...",
    "cta_educativa":"...",
    "hashtags":["..."],
    "fontes_para_conferir":["..."],
    "checklist_revisao":["..."]
  }
}
Inclua na versão revisada todas as chaves acima, preservando a estrutura e quantidade de slides/cenas sempre que possível. Corrija erros claros; para pontos juridicamente incertos, não invente uma solução: mantenha a redação cautelosa e sinalize o que exige confirmação. Se não houver problema, informe isso no resumo, mas não alegue que houve validação de legislação.""" 

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
        "checklist_revisao":["Revisar ortografia, acentuação, gramática e pontuação em português brasileiro","Conferir o emprego correto e consistente dos termos jurídicos; explicar o juridiquês indispensável em linguagem simples","Confirmar todos os fundamentos jurídicos em fonte primária atualizada","Revisar a clareza para o público escolhido","Conferir sobriedade e caráter informativo conforme as regras da OAB"]
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

def configured_api_key(name):
    """Treat blank/example secrets as missing so the app never sends placeholder keys."""
    value=str(secret(name,"") or "").strip()
    upper=value.upper()
    placeholders=("COLE_SUA_CHAVE","SUA_CHAVE","SUBSTITUA_POR","SEU_PROJECT_REF","YOUR_API_KEY","PLACEHOLDER")
    if not value or any(marker in upper for marker in placeholders):
        return ""
    return value


def call_gemini(prompt, system_prompt=SYSTEM):
    key=configured_api_key("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY não configurada nos Secrets do Streamlit")
    configured=secret("GEMINI_MODEL","gemini-3.8-flash")
    candidates=model_candidates(configured,[
        "gemini-3.8-flash","gemini-3.6-flash","gemini-3.5-flash-lite",
        "gemini-2.5-flash-lite","gemini-2.5-flash"
    ])
    failures=[]
    for model in candidates:
        try:
            response=requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key":key},
                json={
                    "systemInstruction":{"parts":[{"text":system_prompt}]},
                    "contents":[{"role":"user","parts":[{"text":prompt}]}],
                    "generationConfig":{"temperature":0.55,"responseMimeType":"application/json"}
                },
                timeout=60
            )
            if not response.ok:
                detail=error_detail(response)
                failures.append(f"{model}: HTTP {response.status_code} ({detail})")
                if response.status_code in (400,404,422,429,500,502,503,504):
                    continue
                raise RuntimeError(f"Gemini HTTP {response.status_code}: {detail}")
            try:
                payload=json.loads(response.json()["candidates"][0]["content"]["parts"][0]["text"])
            except (KeyError,IndexError,TypeError,ValueError) as exc:
                failures.append(f"{model}: resposta JSON inválida ({exc.__class__.__name__})")
                continue
            if not isinstance(payload,dict):
                failures.append(f"{model}: a resposta não é um objeto JSON")
                continue
            payload["_modelo_utilizado"]=model
            return payload
        except requests.RequestException as exc:
            failures.append(f"{model}: falha de conexão ({exc.__class__.__name__})")
    raise RuntimeError("Nenhum modelo Gemini funcionou. "+" | ".join(failures[:5]))

def call_groq(prompt, system_prompt=SYSTEM):
    key=configured_api_key("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY não configurada nos Secrets do Streamlit")
    configured=str(secret("GROQ_MODEL","openai/gpt-oss-120b") or "openai/gpt-oss-120b").strip()
    # The configured model is attempted first, followed by active IDs documented by Groq.
    candidates=model_candidates(configured,[
        "openai/gpt-oss-120b","openai/gpt-oss-20b","qwen/qwen3.8-27b"
    ])
    failures=[]
    user_content=prompt+"\n\nRetorne apenas um objeto JSON válido, sem markdown ou comentários."
    for model in candidates:
        for json_mode in (True,False):
            body={
                "model":model,
                "messages":[
                    {"role":"system","content":system_prompt},
                    {"role":"user","content":user_content}
                ]
            }
            if json_mode:
                body["response_format"]={"type":"json_object"}
            try:
                response=requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"},
                    json=body,
                    timeout=60
                )
            except requests.RequestException as exc:
                failures.append(f"{model}: falha de conexão ({exc.__class__.__name__})")
                break
            if not response.ok:
                detail=error_detail(response)
                failures.append(f"{model} ({'modo JSON' if json_mode else 'modo texto'}): HTTP {response.status_code} ({detail})")
                if json_mode and response.status_code==400:
                    continue
                if response.status_code in (400,403,404,422,429,500,502,503,504):
                    break
                raise RuntimeError(f"Groq HTTP {response.status_code}: {detail}")
            try:
                raw_content=response.json()["choices"][0]["message"]["content"]
                payload=parse_json_response(raw_content)
                if not isinstance(payload,dict):
                    raise ValueError("A resposta não é um objeto JSON.")
            except Exception as exc:
                failures.append(f"{model} ({'modo JSON' if json_mode else 'modo texto'}): resposta inválida ({str(exc)[:160]})")
                if json_mode:
                    continue
                break
            payload["_modelo_utilizado"]=model
            return payload
    raise RuntimeError("Nenhum modelo Groq funcionou. "+" | ".join(failures[:5]))

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

def call_openrouter(prompt, system_prompt=SYSTEM):
    key=configured_api_key("OPENROUTER_API_KEY")
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
                        {"role":"system","content":system_prompt},
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
    key=configured_api_key("OPENROUTER_API_KEY")
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


EMOJI_PATTERN = re.compile(r"[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0E\uFE0F\u200D\u20E3]")

def strip_emojis_deep(value):
    """Remove emojis das strings geradas sem alterar a estrutura dos objetos JSON."""
    if isinstance(value, str):
        return EMOJI_PATTERN.sub("", value)
    if isinstance(value, list):
        return [strip_emojis_deep(item) for item in value]
    if isinstance(value, dict):
        return {key: strip_emojis_deep(item) for key, item in value.items()}
    return value

def generate(subject, fmt, audience, goal, details="", area="Geral / interdisciplinar", funnel_stage=FUNNEL_STAGES[0], tone=TONE_OPTIONS[0], angle=ANGLE_OPTIONS[0], requested_cta=CTA_OPTIONS[0]):
    brief=json.dumps({"assunto":subject,"area_do_direito":area,"publico_segmentado":audience,"formato":fmt,"objetivo_editorial":goal,"etapa_do_funil":funnel_stage,"tom_de_voz":tone,"angulo_editorial":angle,"preferencia_de_cta_educativa":requested_cta,"paleta_visual":st.session_state.get("studio_palette","Azul-marinho & champanhe"),"contexto_fontes_e_restricoes":details},ensure_ascii=False)
    errors=[]
    for name,fn in [("Gemini",call_gemini),("Groq (alternativo)",call_groq),("OpenRouter (alternativo)",call_openrouter)]:
        try:
            result = fn("Crie a peça a partir deste briefing. Não invente referências jurídicas. "+brief)
            return strip_emojis_deep(result), name
        except Exception as e: errors.append(name+": "+str(e))
    result=local_draft(subject,fmt,audience,goal,area,funnel_stage,tone,angle,requested_cta,details)
    result["aviso"]="IA externa indisponível; estrutura local usada. "+" | ".join(errors)
    return result,"Modelo local"

def review_language_and_legal_precision(draft, preferred_provider=""):
    """Revisa português/terminologia e tenta integrações configuradas sem travar no primeiro erro."""
    review_input=json.dumps({
        "conteudo_gerado":{k:v for k,v in draft.items() if not str(k).startswith("_")},
        "instrucoes":[
            "Revisar todo o português brasileiro e a terminologia jurídica.",
            "Preservar sentido e fatos; não inventar fundamentos legais.",
            "Separar correções linguísticas de questões que exigem consulta a fonte primária.",
            "Entregar JSON no esquema definido pelo revisor."
        ]
    },ensure_ascii=False)
    prompt="Faça uma segunda revisão editorial e de precisão terminológica jurídica deste conteúdo. Produza o JSON de revisão solicitado.\n\n"+review_input
    providers=[
        ("Gemini","GEMINI_API_KEY",call_gemini),
        ("Groq (alternativo)","GROQ_API_KEY",call_groq),
        ("OpenRouter (alternativo)","OPENROUTER_API_KEY",call_openrouter)
    ]
    configured=[(name,fn) for name,key_name,fn in providers if configured_api_key(key_name)]
    if not configured:
        raise RuntimeError("Nenhuma chave de IA válida está configurada. Confira GEMINI_API_KEY, GROQ_API_KEY ou OPENROUTER_API_KEY nos Secrets do Streamlit.")
    aliases={"Groq":"Groq (alternativo)","OpenRouter":"OpenRouter (alternativo)"}
    preferred=aliases.get(preferred_provider,preferred_provider)
    ordered=[]
    if preferred in {name for name,_ in configured}:
        ordered.extend((name,fn) for name,fn in configured if name==preferred)
    ordered.extend((name,fn) for name,fn in configured if name!=preferred)
    errors=[]
    for name,fn in ordered:
        try:
            result=fn(prompt,system_prompt=LINGUISTIC_REVIEW_SYSTEM)
            if not isinstance(result,dict) or not isinstance(result.get("versao_revisada"),dict):
                raise ValueError("A IA não retornou a versão revisada no formato esperado.")
            result["_provedor_revisao"]=name
            return result
        except Exception as exc:
            errors.append(name+": "+str(exc))
    raise RuntimeError("Não foi possível executar a revisão. Provedores configurados tentados: "+" | ".join(errors[:3]))



COPYWRITER_SYSTEM = """Você é um copywriter editorial sênior especializado em comunicação jurídica brasileira, conteúdo digital e publicidade ética da advocacia.
Escreva com precisão, naturalidade e domínio de ritmo editorial. O texto deve parecer escrito por um profissional experiente, não por um gerador de frases prontas.

REGRAS EDITORIAIS:
- Adapte uma estrutura AIDA apenas como progressão informativa: abertura que identifica uma dúvida real; contexto; esclarecimento da regra geral; limites, exceções ou próximos passos informativos.
- Não use “desejo” para estimular contratação. Não use escassez, urgência artificial, medo, clickbait, “você não vai acreditar”, “segredo que ninguém conta”, “comente EU QUERO”, “me chame”, “contrate” ou chamadas comerciais.
- Não prometa resultados, não garanta direitos sem considerar requisitos, não estimule litígio e não compare nem engrandeça o profissional.
- Siga o caráter informativo, a discrição e a sobriedade do Provimento 205/2021 da OAB e do Código de Ética e Disciplina. Evite expressões persuasivas voltadas à contratação, autopromoção, mercantilização, oferta de serviços, preços ou resultados de casos.
- NÃO use emojis, carinhas, foguetes, sirenes, checkmarks decorativos ou excesso de exclamações. Evite frases feitas, “vamos mergulhar”, “em um mundo cada vez mais”, “é fundamental entender” e conclusões genéricas.
- Use português brasileiro contemporâneo, ritmo natural, frases de tamanhos variados e exemplos concretos. Explique termos técnicos em linguagem simples sem distorcer seu sentido.
- Não invente artigos, leis, prazos, decisões, estatísticas, fatos ou fontes. Não afirme que uma regra está vigente se a fonte oficial atualizada não foi fornecida. Liste referências que precisam de conferência.
- CTA deve ter utilidade pública: salvar para consulta, compartilhar informação, consultar fonte oficial ou enviar uma dúvida geral para pauta futura.
- Hashtags: zero a três, sem enchimento.
- Se houver insuficiência de contexto, explicite a limitação em vez de completar com suposições.
- Produza copy pronta para edição/publicação e sem rótulos internos como “Hook”, “AIDA”, “Atenção”, “Interesse”, “Desejo”, “CTA” ou “Variação 1” no texto final.

Retorne somente JSON válido, sem markdown, com estas chaves:
{
  "titulo": "título editorial específico",
  "abertura": "primeiras linhas da peça, sem clickbait",
  "copy_principal": "texto principal editável, incluindo a legenda quando o formato pedir",
  "slides": [{"titulo": "título específico", "texto": "texto do slide"}],
  "roteiro": [{"cena": "descrição breve", "fala": "fala natural", "texto_na_tela": "texto curto"}],
  "cta_informativa": "próximo passo informativo",
  "hashtags": [],
  "pontos_juridicos_a_conferir": [],
  "alertas_eticos": [],
  "nota_editorial": "uma frase sobre a abordagem escolhida"
}
Para carrossel, crie de 5 a 7 slides com progressão lógica, sem repetição. Para Reel, roteiro natural de aproximadamente 30 a 45 segundos. Para post estático, uma ideia central e texto curto. Para legenda, escreva uma legenda completa. Para artigo curto, use subtítulos úteis e desenvolvimento coerente. Se a fonte oficial não foi incluída, sinalize os fundamentos legais que dependem de conferência."""


def generate_professional_copy(subject, audience, fmt, area, objective, tone, details=""):
    brief = {
        "tema_juridico": subject,
        "publico_alvo": audience,
        "formato": fmt,
        "area_do_direito": area,
        "objetivo_editorial": objective,
        "tom_de_voz": tone,
        "contexto_e_fontes_fornecidos": details or "Nenhuma fonte primária foi fornecida."
    }
    prompt = (
        "Redija a peça solicitada usando as regras do sistema. A copy deve ser profissional, específica, sem emojis, "
        "sem chamada comercial e sem inventar fundamentos.\n\n"
        + json.dumps(brief, ensure_ascii=False)
    )
    providers = [
        ("Gemini", "GEMINI_API_KEY", call_gemini),
        ("Groq", "GROQ_API_KEY", call_groq),
        ("OpenRouter", "OPENROUTER_API_KEY", call_openrouter),
    ]
    errors = []
    for name, key_name, fn in providers:
        if not configured_api_key(key_name):
            errors.append(name + ": integração não configurada")
            continue
        try:
            result = fn(prompt, system_prompt=COPYWRITER_SYSTEM)
            if not isinstance(result, dict) or not str(result.get("copy_principal", "")).strip():
                raise ValueError("A resposta não trouxe copy_principal.")
            result = strip_emojis_deep(result)
            result["_provedor_copy"] = name
            return result, name
        except Exception as exc:
            errors.append(name + ": " + str(exc)[:220])
    raise RuntimeError("Nenhuma integração de IA conseguiu gerar a copy. " + " | ".join(errors))


DEFAULT_TYPOGRAPHY = "Moderno premium — Montserrat + Inter"
TYPOGRAPHY_SPECS = {
    "Clássico imponente — Playfair Display + Montserrat": {
        "title": "Playfair Display", "body": "Montserrat",
        "description": "Títulos serifados expressivos e corpo limpo; melhor escolha inicial para autoridade clássica."
    },
    "Clássico editorial — Cormorant Garamond + Inter": {
        "title": "Cormorant Garamond", "body": "Inter",
        "description": "Mais editorial e literário, com corpo contemporâneo e legível."
    },
    "Contemporâneo refinado — Cinzel + Lato": {
        "title": "Cinzel", "body": "Lato",
        "description": "Título inspirado em inscrições clássicas e texto de leitura simples."
    },
    "Moderno limpo — Playfair Display + Open Sans": {
        "title": "Playfair Display", "body": "Open Sans",
        "description": "Equilíbrio entre personalidade no título e leitura funcional."
    },
    "Moderno premium — Montserrat + Inter": {
        "title": "Montserrat", "body": "Inter",
        "description": "Títulos fortes, modernos e corpo limpo, com contraste e leitura profissional."
    },
}

def typography_guidance(typography_name=DEFAULT_TYPOGRAPHY, aspect_ratio="4:5"):
    spec = TYPOGRAPHY_SPECS.get(typography_name, TYPOGRAPHY_SPECS[DEFAULT_TYPOGRAPHY])
    canvas = {"4:5": "1080 × 1350 px", "1:1": "1080 × 1080 px", "9:16": "1080 × 1920 px"}.get(aspect_ratio, "1080 × 1350 px")
    return (
        f"Par obrigatório: título em {spec['title']}; corpo e textos auxiliares em {spec['body']}. "
        f"Formato: {aspect_ratio} ({canvas}). Em uma arte de 1080 px de largura, título de capa entre 60–80 px "
        f"(equivalente aproximado a 45–60 pt no Canva), subtítulos entre 32–37 px, corpo de texto entre 24–29 px "
        f"e texto auxiliar/rodapé entre 16–19 px (aproximadamente 12–14 pt). Use negrito apenas para hierarquia, no máximo três níveis tipográficos, "
        f"largura de linha confortável, entrelinha generosa, alto contraste e margens seguras. "
        f"Se o texto exceder a capacidade, simplifique a copy sem alterar a regra jurídica ou peça revisão editorial; "
        f"não reduza o corpo abaixo de 24 px, não aperte o espaçamento e não corte palavras. "
        f"Deixe espaço negativo; capa com título curto e dominante; slides internos com uma ideia principal e poucas linhas. "
        f"Sem emojis, ícones decorativos, texto fluorescente, caixa alta em parágrafos ou excesso de dourado."
    )

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
def palette_values(palette):
    """Accept a named palette or a per-session tuple of background, text, and accent colors."""
    if isinstance(palette,(tuple,list)) and len(palette)==3:
        return tuple(palette)
    return PALETTES[str(palette)]


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

def make_art(title, body, palette, signature="", area="Geral / interdisciplinar", style="Editorial premium", slide_index=1, total_slides=1, funnel_stage="Descoberta", logo_bytes=None, typography=DEFAULT_TYPOGRAPHY, output_height=1350):
    title = str(title or "").strip()
    body = str(body or "").strip()
    generic_titles = {"gancho", "introdução", "introducao", "contexto", "explicação", "explicacao", "aplicação", "aplicacao", "conclusão", "conclusao", "fechamento", "síntese", "sintese", "slide", f"slide {slide_index}"}
    if title.casefold().strip(" :.-") in generic_titles:
        first_sentence = re.split(r"(?<=[.!?])\s+", body, maxsplit=1)[0].strip()
        title = first_sentence if first_sentence and len(first_sentence) <= 95 else "O que isso significa na prática?"
    bg,ink,accent=palette_values(palette)
    bg_rgb=_rgb(bg); ink_rgb=_rgb(ink); accent_rgb=_rgb(accent)
    output_height=max(1080,int(output_height))
    scale_y=output_height/1350
    sy=lambda value: int(round(value*scale_y))
    im=Image.new("RGB",(1080,output_height),bg_rgb); d=ImageDraw.Draw(im)
    for y in range(output_height):
        t=y/max(output_height-1,1); colour=_blend(bg_rgb,_blend(bg_rgb,accent_rgb,.18),t*.72); d.line((0,y,1080,y),fill=colour)
    import imagem
    def font(sz,bold=False):
        return imagem.font(sz, bold, typography, "title" if bold else "body")
    soft=_blend(bg_rgb,accent_rgb,.15); very_soft=_blend(bg_rgb,accent_rgb,.075)
    d.rectangle((0,0,1080,18),fill=accent_rgb); d.ellipse((650,sy(-190),1230,sy(390)),fill=soft); d.ellipse((760,sy(-95),1140,sy(285)),outline=accent_rgb,width=3)
    icon_kind=_topic_icon(area); stage_short=(funnel_stage.split("|")[0].strip() if "|" in funnel_stage else funnel_stage).upper()
    d.rounded_rectangle((70,sy(56),675,sy(102)),radius=18,fill=very_soft,outline=accent_rgb,width=1)
    d.text((91,sy(68)),f"{area.upper()}  /  {stage_short}"[:47],font=font(18,True),fill=ink_rgb)
    _draw_icon(d,icon_kind,850,sy(245),250,ink_rgb,accent_rgb,bg_rgb)
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
        y=wrapped(title,72,sy(470),font(78 if len(str(title))<43 else 64,True),930,ink_rgb,84,3)
        d.rounded_rectangle((72,y+12,280,y+19),radius=3,fill=accent_rgb)
        wrapped(body,72,y+53,font(29),900,ink_rgb,39,7)
    else:
        y=wrapped(title,72,sy(175),font(66 if len(str(title))<42 else 56,True),910,ink_rgb,72,3)
        d.rounded_rectangle((72,y+10,320,y+17),radius=3,fill=accent_rgb);y+=45
        d.rounded_rectangle((54,y-12,1025,sy(1110)),radius=26,outline=_blend(accent_rgb,bg_rgb,.55),width=2)
        d.ellipse((68,y+1,88,y+21),fill=accent_rgb)
        wrapped(body,86,y+45,font(28),855,ink_rgb,39,10)
    d.line((72,sy(1204),1008,sy(1204)),fill=_blend(accent_rgb,bg_rgb,.36),width=2)
    d.text((72,sy(1242)),"Informação geral • confira as fontes e o contexto aplicável",font=font(17),fill=_blend(ink_rgb,bg_rgb,.2))
    if logo_bytes:
        try:
            logo=Image.open(io.BytesIO(logo_bytes)).convert("RGBA");logo.thumbnail((150,70))
            im_rgba=im.convert("RGBA");im_rgba.alpha_composite(logo,(900-logo.width,sy(1170)-logo.height));im=im_rgba.convert("RGB")
        except Exception:pass
    out=io.BytesIO();im.save(out,format="PNG",optimize=True);out.seek(0);return out

def build_gemini_full_piece_prompt(draft, fmt="Carrossel", audience="público geral", office="", oab="", palette_name="Identidade do escritório", visual_style="editorial premium", visual_type="Cena editorial", visual_density="Equilibrada", typography_name=DEFAULT_TYPOGRAPHY, aspect_ratio="4:5"):
    """Separa copy textual, direção visual de imagem e instruções de vídeo para o Gemini."""
    slides = draft.get("slides") or []
    text_copy, image_copy = [], []
    generic_titles = {"gancho", "introdução", "introducao", "contexto", "explicação", "explicacao", "aplicação", "aplicacao", "conclusão", "conclusao", "fechamento", "síntese", "sintese", "slide"}
    for i, slide in enumerate(slides, 1):
        title = str(slide.get("titulo", "")).strip()
        body = str(slide.get("texto", "")).strip()
        visual = str(slide.get("visual", "")).strip()
        generic_title = title.casefold().strip(" :.-") in generic_titles or title.casefold().strip(" :.-") == f"slide {i}"
        if generic_title:
            title_for_prompt = f"Crie um título editorial curto e específico com base no texto do slide {i}. O rótulo interno “{title}” não é título final e não pode aparecer na arte."
            image_title_for_prompt = f"Crie e use um título editorial próprio para o slide {i}; nunca escreva “{title}” como título visível."
        else:
            title_for_prompt = title
            image_title_for_prompt = title
        text_copy.append(f"SLIDE {i}\nTÍTULO EDITORIAL: {title_for_prompt}\nCOPY TEXTUAL: {body}")
        image_copy.append(
            f"FUNDO VISUAL {i} (não renderizar texto)\nCONTEXTO/TEMA: {image_title_for_prompt}\nIDEIA VISUAL SOLICITADA PELO AGENTE: {visual or 'Criar conceito visual específico para o conteúdo.'}\n"
            f"REFERÊNCIA DE COPY (não desenhar na imagem; será sobreposta pelo AdvFlow): {image_title_for_prompt} — {body}"
        )
    roteiro = draft.get("roteiro") or []
    video_lines = []
    for i, scene in enumerate(roteiro, 1):
        if isinstance(scene, dict):
            video_lines.append(
                f"CENA {i}\nCENA/OBJETIVO: {scene.get('cena', '')}\nFALA/NARRAÇÃO: {scene.get('fala', '')}\n"
                f"VISUAL/ENQUADRAMENTO: {scene.get('visual', '')}\nTEXTO NA TELA: {scene.get('texto_na_tela', scene.get('texto', ''))}"
            )
        else:
            video_lines.append(f"CENA {i}: {scene}")
    caption = str(draft.get("legenda", "")).strip()
    hashtags = " ".join(draft.get("hashtags", [])) if isinstance(draft.get("hashtags", []), list) else str(draft.get("hashtags", ""))
    sources = "\n".join("- " + str(x) for x in draft.get("fontes_para_conferir", [])) or "Nenhuma fonte indicada pelo agente; não invente fontes."
    fmt_lower = str(fmt).lower()
    layout_ratio = "9:16" if ("reel" in fmt_lower or "vídeo" in fmt_lower or "video" in fmt_lower) else aspect_ratio
    video_mode = "REEL/VÍDEO: produzir um Reel final de 30–45 segundos, vertical 9:16 (1080×1920), com ritmo natural e narração integral. NÃO comprimir todo o roteiro em um clipe curto nem acelerar a fala. Planejar 5–7 cenas com duração indicada por cena, pausas naturais e transições discretas; manter cada cena tempo suficiente para compreender a fala e ler o texto na tela. Se a ferramenta só gerar clipes de 8–10 segundos, NÃO fingir que isso é o vídeo completo: gerar clipes separados e numerados que, juntos, cubram os 30–45 segundos, mantendo personagem, roupa, cenário, iluminação e identidade visual consistentes, e fornecer a ordem de montagem. Enquadramento obrigatório vertical 9:16, nunca horizontal 16:9; manter rosto, mãos e todos os textos dentro da área segura central, com margens de pelo menos 10% nas laterais e 12% no topo/rodapé. Não cortar cabeça, mãos, legendas ou palavras; usar pouco texto por tela, alto contraste e tamanho legível em celular. A narração deve seguir a copy fornecida sem omitir frases; se não couber, aumentar a duração ou dividir em mais clipes, nunca acelerar. Antes de concluir, verificar proporção, duração total, cortes, ritmo, sincronização entre fala e imagem e legibilidade. Se não puder gerar vídeo diretamente, entregar plano de produção cena a cena com duração, narração completa e prompts individuais prontos."
    image_size = {"4:5": "1080 × 1350 pixels", "1:1": "1080 × 1080 pixels", "9:16": "1080 × 1920 pixels"}.get(layout_ratio, "1080 × 1350 pixels")
    image_mode = f"CARROSSEL/IMAGENS: gere um fundo visual limpo para cada slide, no formato {layout_ratio} ({image_size}), sem letras, números ou texto. A copy será aplicada depois no AdvFlow com fontes reais. Prepare um prompt autônomo por slide, com variação visual relevante e identidade consistente; nunca agrupe imagens em mosaico, grade ou storyboard."
    if "reel" in fmt_lower or "vídeo" in fmt_lower or "video" in fmt_lower:
        media_instructions = video_mode
    elif "legenda" in fmt_lower:
        media_instructions = "FORMATO LEGENDA: priorize a copy textual e ofereça uma proposta visual opcional relacionada ao tema."
    elif "estático" in fmt_lower or "estatico" in fmt_lower:
        media_instructions = "POST ESTÁTICO: crie um fundo visual único, de alta qualidade e sem letras; a copy será aplicada depois pelo AdvFlow com tipografia real."
    else:
        media_instructions = image_mode
    return f"""Você é diretor criativo, designer editorial e produtor audiovisual sênior para conteúdo jurídico brasileiro. Use o conteúdo abaixo para produzir a copy e a peça de mídia solicitada.

ENTREGA DESEJADA
{media_instructions}

IDENTIDADE E DIREÇÃO VISUAL
Paleta: {palette_name}
Estilo visual: {visual_style}
Tipo de composição: {visual_type}
Densidade visual: {visual_density}
SISTEMA TIPOGRÁFICO OBRIGATÓRIO
{typography_guidance(typography_name, layout_ratio)}
Não inclua nome de escritório, nome de advogado, número de OAB, assinatura ou dados institucionais no rodapé. Não invente placeholders como “Nome do escritório”, “OAB: UF 000000” ou equivalentes. Deixe o rodapé limpo. Não acrescente numeração de slides, frações como 1/3 ou marcadores de sequência.
Público-alvo: {audience}
Área jurídica: {draft.get("area_direito", "Geral / interdisciplinar")}
Tom de voz: {draft.get("tom_de_voz", "sóbrio, didático e profissional")}
Título geral: {draft.get("titulo", "Conteúdo jurídico")}
Resumo estratégico: {draft.get("resumo_estrategico", "")}

COPY 1 — TEXTO EDITORIAL (legenda, título, slides e narração)
{chr(10).join(text_copy) if text_copy else "Não há slides estruturados; use apenas os campos fornecidos e não invente fatos."}

COPY 2 — DIREÇÃO CRIATIVA PARA IMAGEM/ARTE (conceito visual, composição, cena e texto a aplicar)
{chr(10).join(image_copy) if image_copy else "Não há direções visuais estruturadas; desenvolva uma proposta visual específica baseada no tema."}

COPY 2B — DIREÇÃO PARA VÍDEO (cenas, fala/narração, enquadramento, movimento e texto na tela)
{chr(10).join(video_lines) if video_lines else "Não há roteiro de vídeo estruturado. Se o formato solicitado for vídeo/Reel, crie um plano de cenas com base somente na copy editorial acima e sinalize qualquer suposição."}

GANCHO: {draft.get("gancho", "")}
CTA EDUCATIVA: {draft.get("cta_educativa", "")}
LEGENDA PARA PUBLICAÇÃO (não precisa ser inserida na imagem):
{caption}
HASHTAGS: {hashtags}
FONTES PARA CONFERIR (não inventar fontes):
{sources}

REGRAS IMPORTANTES
- São duas entregas diferentes: COPY TEXTUAL e COPY/DIREÇÃO DE MÍDIA. Não misture nem substitua uma pela outra.
- Preserve o sentido jurídico e reproduza os textos fornecidos com precisão. Não invente leis, artigos, prazos, estatísticas, decisões, nomes ou fontes.
- Para imagens, entregue o fundo visual sem texto, letras, números, logotipos ou pseudo-texto. A tipografia e a copy serão aplicadas depois pelo AdvFlow para preservar a grafia exata.
- Para vídeo, priorize Reel final de 30–45 segundos em 9:16; não aceite clipe curto ou horizontal como entrega final. Se houver limite de duração, divida em clipes numerados que somem a duração pedida. Não acelere nem corte a narração para caber; preserve áreas seguras e confira cortes, ritmo e legibilidade. Se o Gemini não puder renderizar vídeo nesta conversa, forneça um plano de produção e prompts cena a cena, sem afirmar que o vídeo foi gerado.
- Não remova texto importante para fazê-lo caber; reorganize a composição ou sinalize o problema.
- Respeite a publicidade informativa e discreta da advocacia e as regras aplicáveis da OAB. Sem promessa de resultado, pressão comercial ou captação indevida.
- Revise o português brasileiro antes de produzir as peças: acentuação, ortografia, concordância, regência, pontuação e naturalidade. Preserve a terminologia jurídica correta, evite juridiquês desnecessário e explique termos técnicos quando isso couber na copy.
- Use português brasileiro correto, acentuação correta e texto legível em tela de celular.
- Não inclua nome de escritório, nome de advogado, número de OAB, assinatura ou dados institucionais no rodapé da imagem; não use placeholders fictícios como “Nome do escritório” ou “OAB: UF 000000”. Se não houver solicitação expressa, deixe o rodapé limpo.
- Não invente logotipo nem dados institucionais.
- Não use “Gancho”, “Contexto”, “Slide 1” ou rótulos de roteiro como título final. Crie um título editorial concreto e natural para o público.
- Não use quebra-cabeças com termos jurídicos soltos como metáfora genérica. Prefira uma cena ou objeto concreto que tenha ligação direta com a situação explicada.
- Não exiba juridiquês como lista de palavras soltas. Explique cada termo técnico indispensável em linguagem comum, no próprio slide, sem alterar seu significado jurídico.
- Antes de concluir, confira fidelidade da copy, ortografia, margens e consistência entre as peças.
"""

def build_gemini_carousel_slide_prompt(draft, slide, slide_number, total_slides, office="", oab="", palette_name="Identidade do escritório", typography_name=DEFAULT_TYPOGRAPHY, aspect_ratio="4:5", visual_type="Cena editorial", visual_style="Fotografia editorial premium", visual_density="Equilibrada"):
    """Prompt individual para criar um fundo nítido e sem texto; AdvFlow aplica a tipografia depois."""
    raw_title=str(slide.get("titulo",f"Slide {slide_number}")).strip()
    body=str(slide.get("texto","")).strip()
    visual=str(slide.get("visual","")).strip()
    return f"""Gere UMA imagem de FUNDO sem texto para o slide {slide_number} de um carrossel profissional para Instagram.
A imagem será finalizada depois no AdvFlow. Não desenhe as palavras fornecidas: o título e o corpo serão aplicados pelo renderizador com fontes reais, para preservar a grafia e evitar letras deformadas.

FORMATO E COMPOSIÇÃO
- Proporção {aspect_ratio}: 4:5 = 1080 × 1350 px; 1:1 = 1080 × 1080 px; 9:16 = 1080 × 1920 px.
- Gere somente o fundo do slide {slide_number}; nunca um carrossel inteiro, grade, mosaico, colagem de páginas ou storyboard.
- Design comercial editorial premium, equilibrado e pronto para receber tipografia profissional depois.
- Iluminação de estúdio controlada, sombras deliberadas, materiais nítidos, textura realista, foco preciso no assunto principal e acabamento de revista profissional.
- Contraste claro, cores bem definidas dentro da paleta escolhida; evite aparência apagada, lavada, enevoada, sem contraste ou fora de foco.
- Reserve espaço negativo amplo principalmente à esquerda e na parte inferior para que o AdvFlow aplique título e copy sem cobrir o assunto principal.
- Identidade visual consistente: {palette_name}. Tipografia que será aplicada depois no AdvFlow: {typography_guidance(typography_name, aspect_ratio)}
- Preferências visuais escolhidas: tipo {visual_type}; estilo {visual_style}; densidade {visual_density}. Preserve legibilidade e uma hierarquia visual clara.

CONTEXTO EDITORIAL — APENAS PARA ORIENTAR A CENA; NÃO RENDERIZAR COMO TEXTO
Área do Direito: {draft.get("area_direito","Geral / interdisciplinar")}
Público: {draft.get("publico","público geral")}
Tema geral: {draft.get("titulo","Conteúdo jurídico")}
Título de referência deste slide: {raw_title}
Ideia explicada pelo slide: {body}
Direção visual sugerida: {visual or "Escolha uma cena, um objeto ou uma situação concreta diretamente ligada à ideia."}

RESTRIÇÕES ABSOLUTAS
- Não incluir texto, letras, palavras, pseudo-letras, números, legendas, logotipos, marcas-d'água, caracteres aleatórios, placas legíveis nem elementos que imitem escrita.
- Não adicionar títulos, labels, assinatura, OAB, número do slide, frações como 1/3 ou marcadores de sequência.
- Não usar imagens de documentos reais, nomes, números de processo ou dados de clientes.
- Não usar balança, martelo de juiz, pilha de livros jurídicos, aperto de mãos ou reunião de escritório como clichês automáticos; use-os somente quando forem diretamente relevantes à pauta.
- Não sugerir vitória, indenização garantida ou resultado certo de processo.
- Não reproduzir marcas ou identidade visual de terceiros.
- Produza apenas a imagem final de fundo em alta qualidade, com foco nítido e sem texto. Não responda com explicações.
"""

def build_gemini_plus_prompt(title, body, area, audience, tone, style, palette_name, palette, visual_direction, visual_type="Cena editorial", density="Equilibrada", aspect_ratio="4:5"):
    bg,ink,accent=palette
    styles={
      "Fotografia editorial premium":"high-end editorial photography, premium magazine art direction, photorealistic materials, refined natural window light, soft cinematic shadows, 35mm lens look, deliberate framing, subtle film texture; fotografia editorial sofisticada e verossímil",
      "Ilustração conceitual":"premium conceptual editorial illustration, art-directed visual metaphor, refined paper grain, elegant geometry, layered depth, restrained palette, contemporary museum-catalog finish; ilustração autoral ligada à ideia específica",
      "Colagem contemporânea":"contemporary premium editorial collage, precise photographic cutouts, tactile paper layers, asymmetrical composition, controlled negative space, refined depth and texture; sem aparência de template genérico",
      "3D sofisticado":"high-end 3D still life, physically plausible materials, studio lighting, controlled reflections, sophisticated soft shadows, premium product-render finish; objetos contextualizados ao tema",
      "Documental cinematográfica":"cinematic documentary photography, believable real-world environment, natural directional light, authentic detail, restrained color grading, shallow depth of field when appropriate; sem dramatização exagerada",
      "Infográfico ilustrado":"premium editorial infographic, clean visual hierarchy, precise diagram, a small number of purposeful elements, refined line work and restrained colors; informação visual clara e sem decoração supérflua"
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
    return f"""Create an original, premium editorial background for Brazilian legal communication. Instagram format {aspect_ratio}, to be finished later in AdvFlow. Prefer precise visual storytelling over generic legal stock imagery.

ART DIRECTION
Use these visual keywords as style guidance: high-end corporate editorial, sophisticated, trustworthy, authoritative but calm, photorealistic or carefully art-directed according to the selected style, cinematic lighting, natural materials, strong visual hierarchy, elegant negative space, subtle gold accents only when compatible with the selected palette. Build the scene around the specific argument of this slide, not around a generic idea of “law”.
Style selected: {style}. {styles.get(style,style)}

CONTENT CONTEXT — DO NOT RENDER THIS AS TEXT
Headline: {title}
Slide idea: {str(body)[:700]}
Legal area: {area}
Audience: {audience}
Tone: {tone}

DIREÇÃO DE ARTE
Tipo de composição: {visual_type}. {type_directions.get(visual_type, type_directions["Cena editorial"])}
Estilo: {styles.get(style,style)}.
Densidade: {density}. {density_directions.get(density,density_directions["Equilibrada"])}
Direção visual adicional: {visual_direction or "Interprete o argumento e escolha uma cena, objeto, gesto, material ou metáfora visual diretamente relacionados a esta pauta."}
Paleta: {palette_name}; harmonize with {bg} (background), {ink} (dark ink), and {accent} (accent colour), without forcing every colour into the scene.
Série: mantenha iluminação, tratamento, textura e paleta coerentes com os outros slides, mas mude o cenário e a metáfora de acordo com o argumento de cada slide.

COMPOSIÇÃO E ACABAMENTO
Design comercial editorial premium, equilibrado e pronto para receber tipografia profissional depois. Use iluminação de estúdio deliberada, sombras controladas, materiais nítidos, textura verossímil e foco preciso no assunto principal. Contraste visual claro, cores bem definidas dentro da paleta escolhida e acabamento limpo; evite aparência desbotada, cinzenta, lavada, enevoada ou sem contraste. Reserve espaço negativo útil principalmente à esquerda e na parte inferior para o título e o corpo que serão aplicados pelo AdvFlow. Posicione o assunto visual dominante à direita ou na região superior direita, sem cortar detalhes essenciais. Alta resolução, contornos definidos e composição perfeitamente equilibrada.

RESTRIÇÕES
Não incluir texto, letras, pseudo-letras, palavras, números, legendas, logotipos, marcas-d'água, caracteres aleatórios ou qualquer textura que pareça escrita. Não incluir texto desfocado ao fundo, placas com letras ou elementos gráficos que imitem tipografia. Não usar balança da justiça, martelo de juiz, pilha de livros jurídicos, aperto de mãos, caneta assinando contrato ou pessoas numa reunião de escritório como padrão automático; só usar esses elementos quando forem diretamente relevantes à pauta. Não usar peças de quebra-cabeça com termos jurídicos como metáfora genérica. Não sugerir sucesso, vitória, indenização garantida ou resultado de processo por meio da imagem. Prefira uma situação concreta, objeto ou cena que comunique a dúvida específica do público. Não mostrar documentos reais, nomes, números de processo ou dados de clientes. Não copiar a identidade visual de terceiros. Produza apenas a imagem de fundo, sem tipografia, em alta qualidade; o AdvFlow aplicará a copy com fontes reais na etapa final."""

def build_gemini_final_art_prompt(title, body, area, audience, tone, style, palette_name, palette, visual_direction, visual_type="Cena editorial", density="Equilibrada", aspect_ratio="4:5", typography_name=DEFAULT_TYPOGRAPHY):
    """Prompt para o Gemini criar a peça completa, com a copy renderizada na própria imagem."""
    bg, ink, accent = palette
    style_words = {
        "Fotografia editorial premium": "high-end editorial photography, photorealistic materials, refined natural window light, controlled cinematic shadows, deliberate 35mm-lens-like framing",
        "Ilustração conceitual": "premium conceptual editorial illustration, original concrete visual metaphor, tactile paper grain, elegant geometry, restrained palette",
        "Colagem contemporânea": "contemporary premium editorial collage, precise photographic cutouts, tactile paper layers, asymmetrical composition, controlled negative space",
        "3D sofisticado": "high-end 3D still life, physically plausible materials, studio lighting, controlled reflections, sophisticated soft shadows",
        "Documental cinematográfica": "cinematic documentary photography, believable real-world environment, natural directional light, authentic detail, restrained color grading",
        "Infográfico ilustrado": "premium editorial infographic, precise visual hierarchy, legible typography, restrained graphic elements, clear information design"
    }
    composition = {
        "Cena editorial": "one specific real-world scene directly related to the legal topic, with a clear visual focal point",
        "Conceito visual": "one original and concrete visual metaphor that a non-lawyer can understand immediately",
        "Infográfico sem texto": "a clean visual diagram with a small number of relevant elements, while still rendering the supplied headline and body text as the final overlay",
        "Fluxo de etapas": "a clear step-by-step visual composition with simple directional cues and the exact supplied words",
        "Comparação de conceitos": "a balanced two-part composition that makes the distinction understandable without adding unsupported claims",
        "Linha do tempo visual": "a simple, orderly timeline-style composition; do not invent dates or events",
        "Checklist visual": "a clean editorial checklist composition using only the supplied statements",
        "Colagem editorial": "a refined editorial collage, while keeping text on a clean, high-contrast typographic layer"
    }
    density_words = {
        "Enxuta": "one focal image, sparse supporting elements, generous negative space",
        "Equilibrada": "one dominant focal point, no more than two supporting visual elements, comfortable text space",
        "Rica em detalhes": "rich material texture and contextual detail, while preserving a calm hierarchy and ample legibility"
    }
    return f"""Create ONE COMPLETE, FINISHED Instagram artwork — NOT a background-only image and NOT a text-free image.
Canvas: {aspect_ratio}; use 1080 × 1350 px for 4:5, 1080 × 1080 px for 1:1, and 1080 × 1920 px for 9:16.
The final image must already contain the supplied Portuguese headline and body text, accurately typeset and integrated into the design. The user should not need to add the copy in another editor.

DESIGN DIRECTION
Art direction: high-end corporate editorial, sophisticated, trustworthy, authoritative but calm, premium magazine quality, natural materials, cinematic yet controlled lighting, a coherent color system, elegant negative space, excellent typographic hierarchy.
TYPOGRAPHIC SYSTEM — FOLLOW PRECISELY
{typography_guidance(typography_name, aspect_ratio)}
Selected style: {style_words.get(style, style)}.
Composition type: {composition.get(visual_type, composition["Cena editorial"])}.
Visual density: {density_words.get(density, density_words["Equilibrada"])}.
Specific visual direction: {visual_direction or "Choose a concrete scene or object that directly explains the main idea in this slide, not generic legal stock imagery."}
Color palette: {palette_name}; background {bg}, primary text/ink {ink}, accent {accent}. Use the accent color sparingly and keep text/background contrast high.
Legal topic: {area}. Audience: {audience}. Tone: {tone}.

EXACT PORTUGUESE TEXT TO RENDER IN THE IMAGE
Headline — reproduce exactly, with correct Portuguese spelling and accents:
“{title}”

Body copy — reproduce every word, punctuation mark and accent exactly:
“{body}”

STRICT TEXT REQUIREMENTS
- The headline and body above MUST be visible in the final artwork itself, not only described in your response.
- Do not translate, paraphrase, shorten, rewrite, duplicate, omit or invent any of the supplied text.
- Use Brazilian Portuguese exactly as provided. Check every accent, cedilla, punctuation mark and word before finishing.
- Make all letters crisp, correctly spelled and readable on a phone. Headline must be clearly larger than body copy. Use line breaks and text blocks intelligently.
- If the body is long, rearrange the composition and reduce font size moderately while keeping it readable; never crop or omit words to make it fit.
- Do not insert labels such as “Gancho”, “Slide 1”, “Título”, “Texto”, or production notes as visible design text unless those exact words are part of the supplied copy.
- Do not add any legal definitions, law references, dates, statistics, call to action, hashtags, fake signature or additional copy beyond the two text fields.

COMPOSITION AND SAFETY
- Keep all text well inside safe margins; no clipped letters, text touching the edges, or important visual content cut off.
- Use a specific and appropriate visual metaphor or scene; do not default to puzzle pieces with legal terms, scales of justice, judge's gavel, books of law, handshake or pen signing a document unless directly relevant to this topic.
- No office name, lawyer name, OAB number, placeholder footer, invented logo, watermark, slide counter, fraction such as 1/3, or sequence marker. Leave the footer clean and preserve only the supplied copy.
- Do not imply guaranteed legal outcomes, winning a case or guaranteed compensation.
- Output only the finished image. Do not answer with a description, instructions or a blank background.
"""


def fetch_openrouter_image_models():
    """Fetch the live Image API catalog; returned model metadata controls optional parameters."""
    key=configured_api_key("OPENROUTER_API_KEY")
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

def generate_openrouter_image(prompt, model_id, model_meta=None, reference_image=None, reference_mime="image/png", aspect_ratio="4:5"):
    """Generate one image via OpenRouter's unified Image API; this endpoint can consume credits."""
    key=configured_api_key("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY não configurada nos Secrets do Streamlit.")
    model_id=(model_id or "").strip()
    if not model_id or "/" not in model_id:
        raise RuntimeError("Selecione um identificador de modelo de imagem válido no catálogo OpenRouter.")
    params=_supported_parameter_map(model_meta or {})
    payload={"model":model_id,"prompt":prompt}
    # Pass optional controls only when the live catalog advertises them.
    if "aspect_ratio" in params:
        ratio=_choose_enum_parameter(params.get("aspect_ratio"),[aspect_ratio,"4:5","1:1","9:16","3:4","2:3"])
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
            payload["aspect_ratio"]=aspect_ratio if aspect_ratio in ratio_list else ratio_list[0]
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


def compose_uploaded_art(image_bytes, title, body, palette, signature="", area="Geral / interdisciplinar", slide_index=1, total_slides=1, typography=DEFAULT_TYPOGRAPHY, output_height=1350):
    if not str(title or "").strip() or not str(body or "").strip():
        raise ValueError("Título ou texto da arte vazio. Gere ou preencha o conteúdo antes de finalizar a imagem.")
    import imagem
    photo=ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    if photo.width < 200 or photo.height < 200:
        raise ValueError("A imagem é pequena demais para uma arte profissional. Envie uma imagem com pelo menos 200 × 200 pixels.")
    bg,ink,accent=palette_values(palette)
    # Cover uses the photo-led editorial composition; following slides use a clean typography-led layout.
    focus=imagem.autofoco(photo) or (.5,.4)
    rendered=imagem.render(
        {"titulo":str(title).strip(),"texto":str(body).strip(),"kicker":str(area or "Conteúdo informativo")},
        max(0,int(slide_index)-1),max(1,int(total_slides)),int(output_height),
        (bg,ink,accent),signature,"",photo,
        None,focus,"Centro",typography
    )
    out=io.BytesIO();rendered.save(out,format="PNG",optimize=True);out.seek(0);return out



st.title("AdvFlow")
st.caption("Conteúdo jurídico informativo • agente de IA • criação visual • radar jurídico")
with st.sidebar:
    st.markdown(f"**Conta:** {current_user['username']} · {current_user['role']}")
    if st.button("Sair da conta", key="logout_button"):
        st.session_state.pop("auth_user", None)
        st.rerun()
    st.divider()
    st.subheader("Identidade do escritório")
    office=st.text_input("Nome / assinatura",value=secret("OFFICE_NAME","Seu escritório"))
    oab=st.text_input("Identificação OAB (opcional)",value=secret("OFFICE_OAB",""))
    st.divider()
    st.caption("Use IA como apoio editorial. Revise fontes e conformidade antes de publicar.")
tab_labels=["Painel","Agente de conteúdo","Radar jurídico","Configuração"]
if current_user["role"] == "admin":
    tab_labels.append("Usuários")
tabs=st.tabs(tab_labels)

with tabs[0]:
    st.subheader("Painel")
    st.info("Comece pelo Agente de conteúdo. Crie o conteúdo, revise a redação e a triagem ética, configure a identidade visual uma única vez e finalize as artes na mesma tela.")

with tabs[1]:
    st.subheader("Agente de conteúdo")

    with st.expander("Identidade visual e formato das artes", expanded=True):
        palette_options=list(PALETTES)
        st.session_state.setdefault("studio_palette","Azul-marinho & champanhe")
        chosen_palette=st.selectbox("Paleta visual",palette_options,key="studio_palette")
        if chosen_palette=="Personalizada":
            custom_cols=st.columns(3)
            custom_bg=custom_cols[0].color_picker("Cor de fundo",value=st.session_state.get("custom_bg","#F5F1E8"),key="custom_bg")
            custom_ink=custom_cols[1].color_picker("Cor do texto",value=st.session_state.get("custom_ink","#172842"),key="custom_ink")
            custom_accent=custom_cols[2].color_picker("Cor de destaque",value=st.session_state.get("custom_accent","#B79B61"),key="custom_accent")
            selected_palette_colors=(custom_bg,custom_ink,custom_accent)
        else:
            selected_palette_colors=PALETTES[chosen_palette]

        settings_a,settings_b=st.columns(2)
        visual_type=settings_a.selectbox("Tipo de composição",["Cena editorial","Conceito visual","Infográfico sem texto","Fluxo de etapas","Comparação de conceitos","Linha do tempo visual","Checklist visual","Colagem editorial"],key="studio_visual_type")
        visual_style=settings_b.selectbox("Estilo visual",["Fotografia editorial premium","Ilustração conceitual","Colagem contemporânea","3D sofisticado","Documental cinematográfica","Infográfico ilustrado"],key="studio_visual_style")
        if not st.session_state.get("_modern_typography_default_applied"):
            st.session_state["studio_typography"]=DEFAULT_TYPOGRAPHY
            st.session_state["_modern_typography_default_applied"]=True
        settings_c,settings_d=st.columns(2)
        visual_density=settings_c.selectbox("Densidade visual",["Enxuta","Equilibrada","Rica em detalhes"],index=1,key="studio_visual_density")
        typography_choice=settings_d.selectbox("Combinação tipográfica",list(TYPOGRAPHY_SPECS),index=list(TYPOGRAPHY_SPECS).index(DEFAULT_TYPOGRAPHY),key="studio_typography")
        st.caption(TYPOGRAPHY_SPECS[typography_choice]["description"]+" Se a fonte não puder ser carregada no servidor, será usada uma alternativa compatível.")
        ratio_labels={
            "4:5":"Feed vertical — 4:5 (1080 × 1350)",
            "1:1":"Feed quadrado — 1:1 (1080 × 1080)",
            "9:16":"Reels e Stories — 9:16 (1080 × 1920)"
        }
        selected_ratio=st.selectbox("Proporção da arte",list(ratio_labels),format_func=lambda ratio:ratio_labels[ratio],key="studio_aspect_ratio")
        selected_height={"4:5":1350,"1:1":1080,"9:16":1920}[selected_ratio]

    st.divider()

    with st.expander("Revisão OAB — triagem ética", expanded=False):
        st.subheader("Triagem de publicidade jurídica")
        text_to_check=st.text_area("Cole a legenda, roteiro ou texto da arte",height=220)
        if st.button("Analisar texto"):
            for alert in review(text_to_check):
                if alert.startswith("Nenhum"):st.info(alert)
                else:st.warning(alert)
            st.caption("Triagem lexical preliminar. Não é parecer jurídico nem certificação de conformidade com o Provimento 205/2021 ou outras regras vigentes.")

    brief_version=int(st.session_state.get("brief_version",0))
    subject_key=f"brief_subject_{brief_version}"
    details_key=f"brief_details_{brief_version}"
    with st.form("brief"):
        creation_mode=st.selectbox(
            "Modo de criação",
            ["Conteúdo jurídico completo","Copy profissional"],
            help="Conteúdo jurídico completo monta a peça e seus elementos. Copy profissional prioriza uma redação natural, sóbria e pronta para edição, sem emojis nem apelos comerciais.",
            key="agent_creation_mode"
        )
        subject=st.text_input("Tema jurídico",value=st.session_state.get("news_brief_subject",""),key=subject_key,placeholder="Ex.: direitos do consumidor em compras online")
        fmt=st.selectbox("Formato",["Carrossel","Reel","Post estático","Legenda","Artigo curto"],key="agent_content_format")
        audience=st.text_input("Público-alvo",placeholder="Ex.: consumidores, pequenos empresários",key="agent_content_audience")
        goal=st.selectbox("Objetivo editorial",["Educar","Explicar mudança normativa","Responder dúvida frequente","Desmistificar um conceito","Orientar sobre prevenção"],key="agent_content_goal")
        area=st.selectbox("Área do Direito",LEGAL_AREAS,key="agent_content_area")
        tone=st.selectbox("Tom de voz",TONE_OPTIONS,key="agent_content_tone")
        details=st.text_area("Contexto, fonte ou pontos obrigatórios (opcional)",value=st.session_state.get("news_brief_details",""),key=details_key,height=125)
        submit=st.form_submit_button("Gerar conteúdo",type="primary")
    if submit:
        if not subject.strip():
            st.warning("Informe o tema jurídico.")
        else:
            try:
                if creation_mode == "Copy profissional":
                    copy_format_map={
                        "Legenda":"Legenda para Instagram",
                        "Carrossel":"Carrossel",
                        "Reel":"Reel",
                        "Post estático":"Post estático",
                        "Artigo curto":"Artigo curto"
                    }
                    with st.spinner("Redigindo a copy profissional..."):
                        copy_result,provider=generate_professional_copy(
                            subject, audience or "público geral", copy_format_map.get(fmt,fmt),
                            area, goal, tone, details
                        )
                    normalized_slides=[]
                    for idx, item in enumerate(copy_result.get("slides",[]) or [],1):
                        if isinstance(item,dict):
                            normalized_slides.append({
                                "titulo":str(item.get("titulo") or f"Ideia {idx}"),
                                "texto":str(item.get("texto") or ""),
                                "visual":str(item.get("visual") or "Direção editorial específica ao tema, composição limpa e bastante espaço negativo."),
                                "objetivo_do_slide":str(item.get("objetivo_do_slide") or "Explicar uma ideia central")
                            })
                    normalized_script=[]
                    for item in copy_result.get("roteiro",[]) or []:
                        if isinstance(item,dict):
                            normalized_script.append({
                                "cena":str(item.get("cena") or ""),
                                "fala":str(item.get("fala") or ""),
                                "visual":str(item.get("visual") or item.get("texto_na_tela") or ""),
                                "texto_na_tela":str(item.get("texto_na_tela") or "")
                            })
                    data={
                        "titulo":str(copy_result.get("titulo") or subject),
                        "promessa_editorial":str(copy_result.get("nota_editorial") or "Conteúdo jurídico informativo"),
                        "angulo_editorial":str(goal),
                        "publico":audience or "público geral",
                        "area_direito":area,
                        "tom_de_voz":tone,
                        "etapa_funil":"Educação informativa",
                        "resumo_estrategico":str(copy_result.get("nota_editorial") or "Copy profissional com foco em clareza, precisão e caráter informativo."),
                        "gancho":str(copy_result.get("abertura") or ""),
                        "slides":normalized_slides,
                        "roteiro":normalized_script,
                        "legenda":str(copy_result.get("copy_principal") or ""),
                        "cta_educativa":str(copy_result.get("cta_informativa") or ""),
                        "hashtags":copy_result.get("hashtags",[]) or [],
                        "fontes_para_conferir":copy_result.get("pontos_juridicos_a_conferir",[]) or [],
                        "checklist_revisao":copy_result.get("alertas_eticos",[]) or [],
                        "formato":fmt,
                        "_copy_profissional":True,
                        "_copy_profissional_abertura":str(copy_result.get("abertura") or ""),
                        "_copy_profissional_alertas":copy_result.get("alertas_eticos",[]) or [],
                        "_copy_profissional_pontos_fonte":copy_result.get("pontos_juridicos_a_conferir",[]) or []
                    }
                    for meta_key in ("_modelo_utilizado","_provedor_utilizado"):
                        if copy_result.get(meta_key):
                            data[meta_key]=copy_result[meta_key]
                else:
                    with st.spinner("Preparando rascunho..."):
                        data,provider=generate(
                            subject,fmt,audience or "público geral",goal,details,
                            area=area,tone=tone
                        )
                if details:
                    data["contexto_informado"]=details
                    if "URL original:" in details:
                        source_url=next((line.split(":",1)[1].strip() for line in details.splitlines() if line.startswith("URL original:")), "")
                        if source_url:
                            data["fontes_para_conferir"]=list(data.get("fontes_para_conferir",[]))+["Notícia original para revisão: "+source_url]
                st.session_state.pop("news_brief_subject",None)
                st.session_state.pop("news_brief_details",None)
                st.session_state.pop("news_brief_ready",None)
                st.session_state.pop("language_legal_review",None)
                st.session_state.pop("language_legal_review_for_title",None)
                st.session_state.pop("draft_caption",None)
                st.session_state["draft"]=data
                st.session_state["draft_provider"]=provider
            except Exception as exc:
                st.error("Não foi possível gerar o conteúdo: "+str(exc))
    if "draft" in st.session_state:
        d=st.session_state["draft"]; st.caption("Motor utilizado: "+st.session_state.get("draft_provider",""))
        if d.get("aviso"):st.warning(d["aviso"])
        if d.get("_modelo_utilizado"):
            st.caption("Modelo utilizado: "+str(d["_modelo_utilizado"]))
        st.write("**Título:**",d.get("titulo","")); st.write(d.get("resumo_estrategico",""))
        if d.get("_copy_profissional_abertura"):
            st.markdown("**Abertura editorial**")
            st.write(d["_copy_profissional_abertura"])
        if d.get("_copy_profissional_alertas"):
            with st.expander("Alertas éticos da copy"):
                for alert in d["_copy_profissional_alertas"]:
                    st.write("- "+str(alert))
        current_draft_format = str(fmt if "fmt" in locals() else d.get("formato", "Carrossel")).lower()
        for i,s in enumerate(d.get("slides",[]),1):
            with st.expander(f"{i}. {s.get('titulo','Slide')}"):
                st.write(s.get("texto","")); st.caption(s.get("visual",""))
                if "carrossel" in current_draft_format:
                    slide_prompt = build_gemini_carousel_slide_prompt(
                        d, s, i, len(d.get("slides", [])),
                        office=office, oab=oab,
                        palette_name=chosen_palette,
                        typography_name=typography_choice,
                        aspect_ratio=selected_ratio,
                        visual_type=visual_type, visual_style=visual_style, visual_density=visual_density
                    )
                    st.components.v1.html(
                        f"""<div style="font-family:sans-serif;padding:2px 0;">
                        <button id="copy-carousel-slide" style="background:#2563eb;color:white;border:0;border-radius:8px;padding:9px 14px;font-size:14px;font-weight:600;cursor:pointer;">Copiar prompt deste slide</button>
                        <span id="copy-carousel-slide-status" style="margin-left:10px;font-size:13px;"></span>
                        <script>
                        const promptText = {json.dumps(slide_prompt, ensure_ascii=False)};
                        const btn = document.getElementById('copy-carousel-slide');
                        const status = document.getElementById('copy-carousel-slide-status');
                        btn.addEventListener('click', async () => {{
                          try {{
                            await navigator.clipboard.writeText(promptText);
                            status.textContent = 'Copiado! Cole no Gemini para gerar esta arte.';
                          }} catch (e) {{
                            status.textContent = 'Cópia automática indisponível; copie o texto do prompt na área abaixo.';
                          }}
                        }});
                        </script></div>""",
                        height=52,
                    )
                    with st.expander("Ver prompt completo deste slide"):
                        st.text_area(
                            f"Prompt do slide {i}",
                            value=slide_prompt,
                            height=260,
                            key=f"gemini_carousel_slide_prompt_{i}"
                        )
        if d.get("roteiro"):st.dataframe(pd.DataFrame(d["roteiro"]),use_container_width=True,hide_index=True)
        caption_label = "Copy principal editável" if d.get("_copy_profissional") else "Legenda editável"
        st.text_area(caption_label,d.get("legenda",""),key="draft_caption",height=180 if d.get("_copy_profissional") else 130)
        st.write("**Hashtags:**"," ".join(d.get("hashtags",[])))
        st.write("**Fontes para conferir:**")
        for s in d.get("fontes_para_conferir",[]):st.write("- "+str(s))

        st.divider()
        st.markdown("#### Revisão de português e terminologia jurídica")
        st.caption("Faça uma segunda leitura por IA para identificar erros de português, termos jurídicos imprecisos, juridiquês excessivo e pontos que precisam de fonte oficial. A revisão não substitui a conferência da legislação nem a análise de um profissional.")
        if st.button("Revisar português e precisão jurídica", type="primary", key="run_language_legal_review"):
            try:
                with st.spinner("Revisando linguagem, terminologia e consistência jurídica…"):
                    review_result = review_language_and_legal_precision(
                        d, preferred_provider=st.session_state.get("draft_provider", "")
                    )
                st.session_state["language_legal_review"] = review_result
                st.session_state["language_legal_review_for_title"] = str(d.get("titulo", ""))
            except Exception as exc:
                st.error("A revisão não foi concluída: " + str(exc))
        review_result = st.session_state.get("language_legal_review")
        if review_result and st.session_state.get("language_legal_review_for_title") == str(d.get("titulo", "")):
            st.markdown("**Resultado da revisão**")
            st.caption("Revisor: " + str(review_result.get("_provedor_revisao", "IA")))
            st.write(review_result.get("resumo_da_revisao", "A revisão foi concluída; confira os apontamentos abaixo."))
            issues = review_result.get("problemas_identificados", [])
            if issues:
                st.dataframe(pd.DataFrame(issues), use_container_width=True, hide_index=True)
            else:
                st.success("A IA não identificou problemas explícitos de redação nesta leitura. Isso não garante correção jurídica.")
            source_flags = review_result.get("pontos_que_exigem_fonte", [])
            if source_flags:
                st.warning("Pontos que precisam de conferência em fonte primária:")
                for item in source_flags:
                    st.write("- " + str(item))
            proposed = review_result.get("versao_revisada", {})
            with st.expander("Ver prévia da versão revisada"):
                st.write("**Título:** " + str(proposed.get("titulo", "")))
                for idx, slide in enumerate(proposed.get("slides", []), 1):
                    st.markdown("**Slide " + str(idx) + ": " + str(slide.get("titulo", "")) + "**")
                    st.write(slide.get("texto", ""))
                if proposed.get("roteiro"):
                    st.dataframe(pd.DataFrame(proposed["roteiro"]), use_container_width=True, hide_index=True)
                st.markdown("**Legenda revisada**")
                st.write(proposed.get("legenda", ""))
                st.markdown("**Checklist da revisão**")
                for item in proposed.get("checklist_revisao", []):
                    st.write("- " + str(item))
            if st.button("Aplicar versão revisada ao rascunho", type="primary", key="apply_language_legal_review"):
                revised_draft = dict(proposed)
                for metadata_key in ("_modelo_utilizado", "_provedor_utilizado", "aviso", "contexto_informado"):
                    if metadata_key in d and metadata_key not in revised_draft:
                        revised_draft[metadata_key] = d[metadata_key]
                required_fields = ("titulo", "slides", "legenda", "fontes_para_conferir")
                if not all(field in revised_draft for field in required_fields):
                    st.error("A versão revisada veio incompleta. O rascunho original foi preservado.")
                else:
                    st.session_state["draft"] = revised_draft
                    st.session_state.pop("language_legal_review", None)
                    st.session_state.pop("language_legal_review_for_title", None)
                    st.success("Versão revisada aplicada ao rascunho.")
                    st.rerun()

        if st.button("Salvar rascunho"):
            with db() as c:c.execute("INSERT INTO pieces(created,subject,format,content,status,user_id) VALUES(?,?,?,?,?,?)",(datetime.now().isoformat(timespec="seconds"),d.get("titulo",subject),fmt,json.dumps(d,ensure_ascii=False),"Rascunho",user_id))
            st.success("Rascunho salvo.")
        st.download_button("Baixar conteúdo JSON",json.dumps(d,ensure_ascii=False,indent=2),"advflow_conteudo.json","application/json")
        st.divider()
        st.markdown("#### Enviar as duas copies ao Gemini: texto + imagem/vídeo")
        st.caption("O briefing separa a copy textual da direção criativa de imagem/vídeo. Para Reel, inclui cenas, fala/narração e orientação visual; para carrossel, especifica cada arte.")
        gemini_brief = build_gemini_full_piece_prompt(
            d, fmt=fmt if "fmt" in locals() else str(d.get("formato", "Carrossel")),
            audience=str(d.get("publico", audience if "audience" in locals() else "público geral")),
            office=office, oab=oab,
            palette_name=chosen_palette,
            visual_style=visual_style,
            visual_type=visual_type,
            visual_density=visual_density,
            typography_name=typography_choice,
            aspect_ratio=selected_ratio
        )
        st.text_area("Briefing completo — copy textual + copy de imagem/vídeo", value=gemini_brief, height=420, key="gemini_full_piece_brief")
        st.components.v1.html(
            f"""<div style="font-family: sans-serif; padding: 2px 0;">
            <button id="copy-advflow-brief" style="background:#2563eb;color:white;border:0;border-radius:8px;padding:10px 16px;font-size:14px;font-weight:600;cursor:pointer;">
            Copiar briefing completo</button>
            <span id="copy-advflow-status" style="margin-left:10px;font-size:13px;"></span>
            <script>
            const brief = {json.dumps(gemini_brief, ensure_ascii=False)};
            const btn = document.getElementById('copy-advflow-brief');
            const status = document.getElementById('copy-advflow-status');
            btn.addEventListener('click', async () => {{
              try {{
                await navigator.clipboard.writeText(brief);
                status.textContent = 'Copiado! Agora cole no Gemini.';
              }} catch (e) {{
                status.textContent = 'Não foi possível copiar automaticamente. Selecione o texto acima e copie.';
              }}
            }});
            </script></div>""",
            height=52,
        )
        st.download_button("Baixar briefing completo (.txt)", gemini_brief.encode("utf-8"), "advflow_briefing_gemini.txt", "text/plain", key="download_gemini_full_piece_brief")
        st.link_button("Abrir Gemini para criar a peça", "https://gemini.google.com/", type="primary")
        st.info("O briefing contém duas partes separadas: copy textual e direção criativa de imagem/vídeo. O link abre o Gemini, mas você precisa copiar e colar o briefing. Se a geração de vídeo não estiver disponível na sua conta/conversa, o prompt pede ao Gemini um plano de produção cena a cena em vez de afirmar que gerou o vídeo.")

    st.subheader("Estúdio visual | Gemini Plus, OpenRouter e AdvFlow")
    st.caption("Método editorial: tipo de composição × estilo × paleta. Você pode usar a assinatura do Gemini manualmente ou gerar pela API do OpenRouter, com custo separado.")
    draft=st.session_state.get("draft",{})
    slides=draft.get("slides") or [{"titulo":draft.get("titulo","Tema jurídico"),"texto":draft.get("gancho","Defina a ideia central do conteúdo."),"visual":"Crie uma cena editorial específica para este assunto."}]
    options=[f"{i+1:02d} — {s.get('titulo','Slide')[:65]}" for i,s in enumerate(slides)]
    selected=st.selectbox("Slide que vamos ilustrar",options,index=0)
    slide_idx=options.index(selected); active=slides[slide_idx]
    title=st.text_input("Título da arte",value=active.get("titulo",draft.get("titulo","Tema jurídico")),key=f"studio_title_{slide_idx}")
    body=st.text_area("Texto que aparecerá na arte",value=active.get("texto",""),height=105,key=f"studio_body_{slide_idx}")
    st.caption("Para assuntos densos, mantenha na arte a ideia principal e deixe a explicação completa na legenda. Isso melhora a leitura em celular.")
    visual_direction=st.text_area("Direção visual específica",value=active.get("visual",""),height=90,key=f"studio_direction_{slide_idx}",help="Detalhe objeto, ambiente, gesto, luz, enquadramento e metáfora visual. Evite apenas 'imagem jurídica'.")

    signature=office+(" • "+oab if oab else "")
    area=draft.get("area_direito","Geral / interdisciplinar")
    audience=draft.get("publico","público definido no briefing")
    tone=draft.get("tom_de_voz","sóbrio, didático e profissional")
    prompt=build_gemini_plus_prompt(title,body,area,audience,tone,visual_style,chosen_palette,selected_palette_colors,visual_direction,visual_type,visual_density,selected_ratio)
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
            if not configured_api_key("OPENROUTER_API_KEY"):
                st.error("Configure OPENROUTER_API_KEY em Streamlit → Settings → Secrets.")
            elif not confirm_cost:
                st.warning("Marque a confirmação de custos antes de executar a geração.")
            else:
                reference_bytes=image_reference.getvalue() if image_reference else None
                reference_mime=(image_reference.type if image_reference else "image/png")
                try:
                    with st.spinner("Gerando imagem profissional com OpenRouter…"):
                        generated=generate_openrouter_image(prompt,chosen_model,model_metadata,reference_bytes,reference_mime,selected_ratio)
                        final_art=compose_uploaded_art(generated["image_bytes"],title,body,selected_palette_colors,signature,area,slide_idx+1,len(slides),typography_choice,selected_height)
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
        st.markdown("#### Gemini Plus: gerar o fundo e aplicar texto nítido no AdvFlow")
        st.caption("Para evitar letras distorcidas, a IA cria somente a imagem de fundo. Depois, o AdvFlow compõe o título e o texto usando a fonte selecionada, preservando a grafia exata.")
        st.text_area("Prompt do fundo — sem texto",value=prompt,height=380,key="gemini_background_prompt")
        st.components.v1.html(
            f"""<div style="font-family:sans-serif;padding:2px 0;">
            <button id="copy-gemini-background-prompt" style="background:#2563eb;color:white;border:0;border-radius:8px;padding:10px 16px;font-size:14px;font-weight:600;cursor:pointer;">Copiar prompt do fundo</button>
            <span id="copy-gemini-background-status" style="margin-left:10px;font-size:13px;"></span>
            <script>
            const bgPromptText = {json.dumps(prompt, ensure_ascii=False)};
            const bgBtn = document.getElementById('copy-gemini-background-prompt');
            const bgStatus = document.getElementById('copy-gemini-background-status');
            bgBtn.addEventListener('click', async () => {{
              try {{ await navigator.clipboard.writeText(bgPromptText); bgStatus.textContent = 'Copiado! Cole no Gemini.'; }}
              catch (e) {{ bgStatus.textContent = 'Selecione o texto acima e copie.'; }}
            }});
            </script></div>""",
            height=52,
        )
        st.download_button("Baixar prompt do fundo (.txt)",prompt.encode("utf-8"),"advflow_prompt_fundo_sem_texto.txt","text/plain",key="download_gemini_background_prompt")
        st.link_button("Abrir Gemini", "https://gemini.google.com/", type="primary")
        uploaded_gemini_background=st.file_uploader(
            "Depois de gerar no Gemini, envie a imagem de fundo sem texto",
            type=["png","jpg","jpeg","webp"],
            key=f"gemini_background_image_{slide_idx}",
            help="Salve a imagem do Gemini sem letras nem logotipos. O AdvFlow aplica o texto em uma camada tipográfica separada."
        )
        if uploaded_gemini_background and st.button("Aplicar tipografia e finalizar arte",type="primary",key=f"finalize_gemini_art_{slide_idx}"):
            try:
                with st.spinner("Aplicando tipografia nítida e finalizando a arte..."):
                    finished=compose_uploaded_art(
                        uploaded_gemini_background.getvalue(),title,body,selected_palette_colors,
                        signature,area,slide_idx+1,len(slides),typography_choice,selected_height
                    )
                st.session_state[f"gemini_background_final_{slide_idx}"]=finished.getvalue()
                st.session_state[f"gemini_background_final_name_{slide_idx}"]=f"advflow_gemini_slide_{slide_idx+1:02d}.png"
                st.success("Arte finalizada: o texto foi composto pelo AdvFlow, não desenhado pela IA.")
            except Exception as exc:
                st.error("Não foi possível finalizar a arte: "+str(exc))
        final_key=f"gemini_background_final_{slide_idx}"
        if st.session_state.get(final_key):
            st.image(st.session_state[final_key],use_container_width=True,caption="Fundo do Gemini + tipografia do AdvFlow")
            st.download_button(
                "Baixar arte final PNG",st.session_state[final_key],
                st.session_state.get(f"gemini_background_final_name_{slide_idx}",f"advflow_gemini_slide_{slide_idx+1:02d}.png"),
                "image/png",key=f"download_gemini_background_final_{slide_idx}"
            )

    with st.expander("Alternativa sem IA"):
        st.caption("A arte local tem ilustração vetorial e tipografia, mas é menos rica que uma imagem editorial criada por IA.")
        if st.button("Gerar arte tipográfica local",key="make_local_studio_art"):
            buf=make_art(title,body,selected_palette_colors,signature,area,"Editorial premium",slide_idx+1,len(slides),draft.get("etapa_funil","Descoberta"),typography=typography_choice,output_height=selected_height)
            st.image(buf,use_container_width=True)
            st.download_button("Baixar arte local PNG",buf.getvalue(),"advflow_arte_local.png","image/png",key="download_local_studio_art")


with tabs[2]:
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

with tabs[3]:
    st.subheader("Integrações e configuração")
    st.markdown("### Estado do login")
    if persistent_auth_enabled():
        try:
            stored_users = supabase_auth_request("GET", params={"select":"username", "limit":"1"})
            st.success("Login persistente configurado: as contas são lidas do Supabase.")
            if st.button("Verificar banco de login", key="test_persistent_login_store"):
                try:
                    users = persistent_auth_users_list()
                    st.success(f"Conexão e tabela confirmadas. Contas cadastradas: {len(users)}.")
                except Exception as exc:
                    st.error("Falha ao verificar o banco persistente: " + str(exc))
        except Exception as exc:
            st.error("Supabase está configurado, mas o banco de login não respondeu: " + str(exc))
    else:
        st.warning("Login ainda usa SQLite local. Configure SUPABASE_URL e SUPABASE_SECRET_KEY para não perder as contas após reinícios/redeploys.")
        st.markdown("Execute primeiro o arquivo [supabase/schema.sql no GitHub](https://github.com/henriquetrader281-pixel/advflow/blob/main/supabase/schema.sql) no SQL Editor do Supabase. Depois adicione a URL do projeto e a chave secreta em Streamlit Community Cloud → Settings → Secrets e reinicie o app.")
    st.markdown("- **Gemini:** configure GEMINI_API_KEY; o fluxo tenta modelos alternativos se o principal estiver ocupado ou indisponível.")
    st.markdown("- **Alternativa Groq:** configure GROQ_API_KEY; o fluxo tenta GPT-OSS 120B, GPT-OSS 20B e Qwen 3.8 27B e repete sem modo JSON quando necessário.")
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
    st.markdown("- **Instagram/Meta:** publicação automática, Insights e agendamento não estão conectados nesta versão.")
    st.markdown("- **Armazenamento de conteúdo:** os rascunhos salvos usam SQLite local; migre os dados para um banco persistente antes de depender deles em produção.")
    st.markdown("- **Login:** se SUPABASE_URL e SUPABASE_SECRET_KEY estiverem configurados, as contas e hashes de senha ficam em public.advflow_users no Supabase. Caso contrário, o login usa SQLite local e pode ser perdido. Senhas usam PBKDF2, cinco falhas bloqueiam o acesso por 15 minutos e cada conta vê apenas os próprios dados.")
    st.markdown("- **Recuperação:** o administrador redefine a senha; o usuário recebe uma senha temporária e precisa trocá-la. Não há envio de e-mails nesta versão.")
    st.markdown("- **Segurança:** nunca coloque chaves de API nem senhas no GitHub. Configure chaves no painel Secrets do Streamlit.")
    st.markdown("- **Persistência:** o SQLite pode não sobreviver a reinícios/redeploys de algumas hospedagens. Configure banco persistente e backup antes de usar em produção.")
    st.markdown("Links: [Gemini API](https://aistudio.google.com/apikey) · [Groq](https://console.groq.com/keys) · [Streamlit Community Cloud](https://share.streamlit.io)")




if current_user["role"] == "admin":
    with tabs[4]:
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
                    auth_user_insert(clean_new,salt,digest,"admin" if role_admin else "user",True,True,datetime.now().isoformat(timespec="seconds"))
                    st.session_state["temporary_credentials"]={"username":clean_new,"password":temporary_password,"message":"Conta criada. Copie a senha temporária agora; ela será ocultada ao sair desta tela."}
                except Exception as exc:
                    if "409" in str(exc) or "duplicate key" in str(exc).lower() or "unique constraint" in str(exc).lower():
                        st.error("Esse nome de usuário já existe.")
                    else:
                        st.error("Não foi possível criar a conta: " + str(exc))
        if st.session_state.get("temporary_credentials"):
            creds=st.session_state["temporary_credentials"]
            st.warning(creds["message"])
            st.code("Usuário: "+creds["username"]+"\nSenha temporária: "+creds["password"])
            if st.button("Ocultar credencial temporária",key="hide_temporary_credentials"):
                st.session_state.pop("temporary_credentials",None)
                st.rerun()
        st.divider()
        try:
            account_rows=auth_users_list()
            account_rows.sort(key=lambda row: str(row.get("username","")).lower())
        except Exception as exc:
            account_rows=[]
            st.error("Não foi possível carregar as contas: " + str(exc))
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
                try:
                    auth_user_update(selected_user, {
                        "password_salt":salt, "password_hash":digest, "must_change_password":True,
                        "failed_attempts":0, "locked_until":None,
                    })
                    st.session_state["temporary_credentials"]={"username":selected_user,"password":temp,"message":"Senha redefinida. Entregue por canal seguro; o usuário deverá trocá-la."}
                    st.rerun()
                except Exception as exc:
                    st.error("Não foi possível salvar a redefinição no banco de contas: " + str(exc))
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
                            try:
                                auth_user_update(selected_user, {"active":False})
                                st.success("Conta desativada."); st.rerun()
                            except Exception as exc:
                                st.error("Não foi possível desativar a conta: " + str(exc))
                    b3.button("Reativar conta",disabled=True,key="reactivate_disabled")
                else:
                    b2.button("Desativar conta",disabled=True,key="disable_disabled")
                    if b3.button("Reativar conta",key="enable_user"):
                        try:
                            auth_user_update(selected_user, {"active":True,"failed_attempts":0,"locked_until":None})
                            st.success("Conta reativada."); st.rerun()
                        except Exception as exc:
                            st.error("Não foi possível reativar a conta: " + str(exc))
