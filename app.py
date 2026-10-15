import os, re, json, sqlite3, io
from datetime import datetime, date
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
    c.execute("CREATE TABLE IF NOT EXISTS pieces(id INTEGER PRIMARY KEY, created TEXT, subject TEXT, format TEXT, content TEXT, status TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY, post_date TEXT, topic TEXT, format TEXT, reach INTEGER, likes INTEGER, comments INTEGER, saves INTEGER, shares INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS calendar(id INTEGER PRIMARY KEY, pub_date TEXT, pub_time TEXT, title TEXT, format TEXT, status TEXT, notes TEXT)")

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
    model=secret("GEMINI_MODEL","gemini-2.5-flash")
    r=requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",params={"key":key},
      json={"systemInstruction":{"parts":[{"text":SYSTEM}]},"contents":[{"role":"user","parts":[{"text":prompt}]}],"generationConfig":{"temperature":0.55,"responseMimeType":"application/json"}},timeout=60)
    if not r.ok: raise RuntimeError(f"Gemini HTTP {r.status_code}")
    return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
def call_groq(prompt):
    key=secret("GROQ_API_KEY")
    if not key: raise RuntimeError("GROQ_API_KEY não configurada")
    model=secret("GROQ_MODEL","llama-3.3-70b-versatile")
    r=requests.post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":"Bearer "+key},
      json={"model":model,"temperature":0.55,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":prompt}]},timeout=60)
    if not r.ok: raise RuntimeError(f"Groq HTTP {r.status_code}")
    return json.loads(r.json()["choices"][0]["message"]["content"])
def generate(subject, fmt, audience, goal):
    brief=json.dumps({"assunto":subject,"formato":fmt,"publico":audience,"objetivo":goal},ensure_ascii=False)
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
st.caption("Estúdio de conteúdo jurídico informativo • assistente de IA • calendário • métricas • radar RSS")
with st.sidebar:
    st.subheader("Identidade do escritório")
    office=st.text_input("Nome / assinatura",value=secret("OFFICE_NAME","Seu escritório"))
    oab=st.text_input("Identificação OAB (opcional)",value=secret("OFFICE_OAB",""))
    palette=st.selectbox("Paleta visual",list(PALETTES))
    st.divider()
    st.caption("Use IA como apoio editorial. Revise fontes e conformidade antes de publicar.")
tabs=st.tabs(["Painel","Agente de conteúdo","Estúdio de imagens","Métricas","Radar de notícias","Calendário","Revisão OAB","Configuração"])

with tabs[0]:
    st.subheader("Painel")
    with db() as c:
        a=c.execute("SELECT COUNT(*) n FROM pieces").fetchone()["n"]
        b=c.execute("SELECT COUNT(*) n FROM metrics").fetchone()["n"]
        cal=c.execute("SELECT COUNT(*) n FROM calendar WHERE status!='Publicado'").fetchone()["n"]
    x,y,z=st.columns(3); x.metric("Peças salvas",a); y.metric("Registros de métricas",b); z.metric("Itens no calendário",cal)
    st.info("Comece pelo Agente de conteúdo. O AdvFlow salva peças, métricas e calendário no SQLite.")

with tabs[1]:
    st.subheader("Agente de conteúdo")
    with st.form("brief"):
        subject=st.text_input("Tema jurídico",placeholder="Ex.: direitos do consumidor em compras online")
        fmt=st.selectbox("Formato",["Carrossel","Reel","Post estático","Legenda"])
        audience=st.text_input("Público-alvo",placeholder="Ex.: consumidores, pequenos empresários")
        goal=st.selectbox("Objetivo editorial",["Educar","Explicar mudança normativa","Responder dúvida frequente","Desmistificar um conceito","Orientar sobre prevenção"])
        details=st.text_area("Contexto, fonte ou pontos obrigatórios (opcional)")
        submit=st.form_submit_button("Gerar conteúdo",type="primary")
    if submit:
        if not subject.strip(): st.warning("Informe o tema.")
        else:
            with st.spinner("Preparando rascunho…"): data,provider=generate(subject,fmt,audience or "público geral",goal)
            if details:data["contexto_informado"]=details
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
            with db() as c:c.execute("INSERT INTO pieces(created,subject,format,content,status) VALUES(?,?,?,?,?)",(datetime.now().isoformat(timespec="seconds"),d.get("titulo",subject),fmt,json.dumps(d,ensure_ascii=False),"Rascunho"))
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
        with db() as c:c.execute("INSERT INTO metrics(post_date,topic,format,reach,likes,comments,saves,shares) VALUES(?,?,?,?,?,?,?,?)",(str(mdate),topic,mformat,reach,likes,comments,saves,shares))
        st.success("Métricas registradas.")
    with db() as c: rows=[dict(r) for r in c.execute("SELECT * FROM metrics ORDER BY post_date DESC")]
    if rows:
        df=pd.DataFrame(rows); df["engajamentos"]=df[["likes","comments","saves","shares"]].sum(axis=1)
        df["engajamento/alcance %"]=(df["engajamentos"]/df["reach"].replace(0,pd.NA)*100).round(2)
        st.dataframe(df,use_container_width=True,hide_index=True)
        st.download_button("Exportar CSV",df.to_csv(index=False).encode("utf-8-sig"),"advflow_metricas.csv","text/csv")
        st.bar_chart(df.groupby("format")[["likes","comments","saves","shares"]].sum())

with tabs[4]:
    st.subheader("Radar de notícias")
    defaults="https://www.conjur.com.br/rss.xml\nhttps://www.migalhas.com.br/rss/quentes"
    rss=st.text_area("URLs de feeds RSS (uma por linha)",value=secret("RSS_URLS",defaults),height=100)
    if st.button("Buscar notícias RSS"):
        entries=[]
        for url in [u.strip() for u in rss.splitlines() if u.strip()]:
            try:
                feed=feedparser.parse(url)
                for e in feed.entries[:15]:entries.append({"fonte":feed.feed.get("title",url),"título":e.get("title","Sem título"),"publicado":e.get("published",""),"link":e.get("link",""),"resumo":re.sub("<[^>]+>"," ",e.get("summary",""))[:450]})
            except Exception as ex:st.warning(f"Falha ao ler {url}: {ex}")
        st.session_state["news"]=entries
    for item in st.session_state.get("news",[]):
        with st.expander(item["título"]):
            st.caption(f'{item["fonte"]} • {item["publicado"]}'); st.write(item["resumo"])
            if item["link"]:st.markdown(f'[Abrir notícia]({item["link"]})')
    st.caption("RSS serve como radar de pauta, não como fonte jurídica definitiva. Confirme em fonte oficial.")

with tabs[5]:
    st.subheader("Calendário editorial")
    with st.form("calform"):
        cd=st.date_input("Data de publicação",value=date.today(),key="cd"); ct=st.time_input("Horário")
        title=st.text_input("Título / pauta"); cf=st.selectbox("Formato",["Carrossel","Reel","Post estático","Story","Artigo"])
        status=st.selectbox("Status",["Ideia","Planejado","Em produção","Em revisão","Aprovado","Publicado"]); notes=st.text_input("Observações")
        add=st.form_submit_button("Adicionar ao calendário")
    if add and title.strip():
        with db() as c:c.execute("INSERT INTO calendar(pub_date,pub_time,title,format,status,notes) VALUES(?,?,?,?,?,?)",(str(cd),str(ct),title,cf,status,notes))
        st.success("Item adicionado.")
    with db() as c: calendar=[dict(r) for r in c.execute("SELECT * FROM calendar ORDER BY pub_date,pub_time")]
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
    st.markdown("- **Gemini:** chave GEMINI_API_KEY; modelo padrão gemini-2.5-flash.")
    st.markdown("- **Alternativa Groq:** chave GROQ_API_KEY; modelo padrão llama-3.3-70b-versatile.")
    st.markdown("- **RSS:** feeds públicos configuráveis.")
    st.markdown("- **Instagram/Meta:** métricas são manuais; publicação, Insights e agendamento não estão conectados.")
    st.markdown("- **Armazenamento:** SQLite local. No Streamlit Cloud, o disco local pode não ser persistente; configure banco externo para uso duradouro.")
    st.markdown("- **Segurança:** não cole segredos no código nem no GitHub. Configure secrets no painel do Streamlit.")
    st.markdown("Links: [Gemini API](https://aistudio.google.com/apikey) · [Groq](https://console.groq.com/keys) · [Streamlit Community Cloud](https://share.streamlit.io)")
