"""
arXiv Daily Digest — Operator Algebras · Free Probability · Random Matrix Theory
Fetches recent papers, ranks by relevance, emails top 3 with bilingual summaries.
"""
import os, sys, json, datetime, smtplib, urllib.request, urllib.error, xml.etree.ElementTree as ET
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Optional

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PW  = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
TO_EMAIL      = os.environ.get("TO_EMAIL", "").strip() or GMAIL_ADDRESS
LLM_API_KEY   = os.environ.get("LLM_API_KEY", "").strip()
LLM_API_URL   = "https://api.deepseek.com/v1/chat/completions"
LLM_MODEL     = "deepseek-chat"

ARXIV_CATS = ["math.OA", "math.PR", "math.FA", "math.MP", "quant-ph"]

KW_HIGH = [
    "operator algebra", "c*-algebra", "von neumann algebra",
    "free probability", "random matrix", "noncommutative",
    "cuntz algebra", "k-theory", "subfactor",
    "free entropy", "free convolution", "free independence",
    "wigner matrix", "eigenvalue distribution", "spectral distribution",
    "quantum information", "quantum group"
]
KW_MEDIUM = [
    "classification", "nuclear dimension", "amenable", "cartan subalgebra",
    "bounded cohomology", "approximation property", "universality",
    "largest eigenvalue", "tracy-widom", "gaussian orthogonal ensemble",
    "gaussian unitary ensemble", "graph of operators", "operator space",
    "tensor category", "kirchberg", "elliott"
]

def fetch_papers(cats: List[str], max_results: int = 150, days_back: int = 2) -> List[Dict]:
    q = "+OR+".join(f"cat:{c}" for c in cats)
    url = (f"http://export.arxiv.org/api/query?search_query={q}"
           f"&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending")
    req = urllib.request.Request(url, headers={"User-Agent": "Digest/1.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=45)
    except Exception as e:
        print(f"[FATAL] arXiv API: {e}")
        return []
    root = ET.fromstring(resp.read().decode("utf-8"))
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_back)
    papers = []
    for e in root.findall("atom:entry", ns):
        title_el = e.find("atom:title", ns)
        summ_el  = e.find("atom:summary", ns)
        pub_el   = e.find("atom:published", ns)
        id_el    = e.find("atom:id", ns)
        prim_el  = e.find("arxiv:primary_category", ns)
        authors  = [a.find("atom:name", ns).text for a in e.findall("atom:author", ns)]
        cats_els = [c.get("term") for c in e.findall("atom:category", ns) if c.get("term")]
        primary  = prim_el.get("term") if prim_el is not None else (cats_els[0] if cats_els else "")
        title    = title_el.text.strip().replace("\n"," ") if title_el is not None and title_el.text else ""
        summary  = summ_el.text.strip().replace("\n"," ")  if summ_el is not None and summ_el.text else ""
        aid_full = id_el.text.strip() if id_el is not None and id_el.text else ""
        aid      = aid_full.split("/abs/")[-1]
        pub_str  = pub_el.text.strip() if pub_el is not None and pub_el.text else ""
        try:
            pub_dt = datetime.datetime.strptime(pub_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        except:
            pub_dt = datetime.datetime.now(datetime.timezone.utc)
        if pub_dt < cutoff:
            continue
        papers.append(dict(title=title, summary=summary, authors=authors,
                           arxiv_id=aid, url=f"https://arxiv.org/abs/{aid}",
                           published=pub_dt, categories=cats_els, primary_category=primary))
    return papers

def score_paper(p: Dict) -> float:
    t = (p["title"] + " " + p["summary"]).lower()
    s = 0.0
    for kw in KW_HIGH:
        n = t.count(kw)
        if n:
            s += n * 10.0
            if kw in p["title"].lower():
                s += 20.0
    for kw in KW_MEDIUM:
        n = t.count(kw)
        if n:
            s += n * 5.0
            if kw in p["title"].lower():
                s += 10.0
    if p["primary_category"] == "math.OA":  s += 15.0
    elif p["primary_category"] == "math.PR": s += 8.0
    elif p["primary_category"] == "math.FA": s += 8.0
    s += len(p["categories"]) * 3.0
    if len(p["authors"]) == 1: s += 2.0
    elif len(p["authors"]) >= 4: s += 2.0
    return s

def llm_rank(papers: List[Dict]) -> Optional[List[Dict]]:
    if not LLM_API_KEY or len(papers) <= 3:
        return None
    buf = ""
    for i, p in enumerate(papers):
        buf += (f"Paper {i+1}:\nTitle: {p['title']}\n"
                f"Authors: {', '.join(p['authors'][:5])}\n"
                f"Abstract: {p['summary'][:800]}\n"
                f"arXiv ID: {p['arxiv_id']}\nCategories: {', '.join(p['categories'])}\n\n")
    prompt = f"""You are a mathematician specializing in operator algebras, free probability, and random matrix theory.
From the list below, pick the 3 most important NEW papers. Rank by novelty, depth, relevance.

Return ONLY this JSON (no markdown):
{{"papers":[
  {{"arxiv_id":"xxxx.xxxxx","rank":1,
    "reason_english":"Why important (1-2 sentences)",
    "reason_japanese":"重要性 (1-2文 日本語)",
    "summary_english":"Technical summary (3-4 sentences)",
    "summary_japanese":"技術的要約 (3-4文 日本語)"}}
]}}

{buf}"""
    try:
        body = json.dumps({"model": LLM_MODEL, "messages": [{"role":"user","content":prompt}],
                           "temperature":0.3, "max_tokens":3000})
        req = urllib.request.Request(LLM_API_URL, data=body.encode(),
                                     headers={"Content-Type":"application/json",
                                              "Authorization":f"Bearer {LLM_API_KEY}"})
        raw = json.loads(urllib.request.urlopen(req, timeout=90).read())
        content = raw["choices"][0]["message"]["content"].strip()
        if content.startswith("```"): content = content.split("\n",1)[1]
        if content.endswith("```"):   content = content[:-3]
        sel = json.loads(content).get("papers", [])
        pmap = {p["arxiv_id"]: p for p in papers}
        out = []
        for s in sel:
            aid = s.get("arxiv_id","")
            if aid in pmap:
                pp = pmap[aid].copy()
                for k in ("rank","reason_english","reason_japanese","summary_english","summary_japanese"):
                    pp[k] = s.get(k,"")
                out.append(pp)
        out.sort(key=lambda x: x.get("rank",99))
        return out[:3] if out else None
    except Exception as e:
        print(f"[WARN] LLM failed: {e}")
        return None

def build_html(papers: List[Dict], used_llm: bool) -> str:
    today = datetime.date.today().strftime("%Y-%m-%d (%a)")
    h = [f"<html><body style='font-family:sans-serif'>",
         f"<h1>📚 arXiv Daily Digest — {today}</h1>",
         f"<p><b>Operator Algebras · Free Probability · Random Matrix Theory</b></p>",
         f"<p>Mode: {'🤖 LLM' if used_llm else '🔑 Keyword'}</p><hr>"]
    for i, p in enumerate(papers):
        h.append(f"<h2>#{i+1} {p['title']}</h2>")
        h.append(f"<p><b>Authors:</b> {', '.join(p['authors'])}</p>")
        h.append(f"<p><b>arXiv:</b> <a href='{p['url']}'>{p['arxiv_id']}</a> · "
                 f"<b>Categories:</b> {', '.join(p.get('categories',[]))}</p>")
        if p.get("reason_english"):  h.append(f"<p><b>Why:</b> {p['reason_english']}</p>")
        if p.get("reason_japanese"): h.append(f"<p><b>重要性:</b> {p['reason_japanese']}</p>")
        h.append(f"<h3>📝 English Summary</h3><p>{p.get('summary_english', p['summary'][:600])}</p>")
        if p.get("summary_japanese"):
            h.append(f"<h3>🇯🇵 日本語要約</h3><p>{p['summary_japanese']}</p>")
        h.append("<hr>")
    h.append("<p style='font-size:small;color:#aaa'>arXiv Daily Digest · GitHub Actions</p></body></html>")
    return "\n".join(h)

def send_email(html: str):
    if not GMAIL_ADDRESS or not GMAIL_APP_PW:
        print("[SKIP] Gmail secrets not set.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"arXiv Daily Digest — {datetime.date.today()}"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = TO_EMAIL
    msg.attach(MIMEText(html, "html", "utf-8"))
    errors = []
    for method, host, port in [("STARTTLS","smtp.gmail.com",587),("SSL","smtp.gmail.com",465)]:
        try:
            if method == "SSL":
                s = smtplib.SMTP_SSL(host, port, timeout=20, local_hostname="github-actions")
            else:
                s = smtplib.SMTP(host, port, timeout=20, local_hostname="github-actions")
                s.ehlo(); s.starttls(); s.ehlo()
            s.login(GMAIL_ADDRESS, GMAIL_APP_PW)
            s.sendmail(GMAIL_ADDRESS, [TO_EMAIL], msg.as_string())
            s.quit()
            print(f"[OK] Email sent via {method}:{port} → {TO_EMAIL}")
            return
        except smtplib.SMTPAuthenticationError:
            print("[FATAL] Gmail auth rejected. Check GMAIL_APP_PASSWORD.")
            sys.exit(1)
        except Exception as e:
            errors.append(f"{method}:{port} → {e}")
    print(f"[ERROR] All SMTP methods failed: {errors}")
    sys.exit(1)

def main():
    print(f"[{datetime.datetime.now().isoformat()}] === ARXIV DIGEST START ===")
    papers = fetch_papers(ARXIV_CATS)
    print(f"[INFO] Fetched {len(papers)} recent papers")
    if not papers:
        print("[DONE] No recent papers.")
        return
    top = llm_rank(papers) if LLM_API_KEY else None
    used_llm = top is not None
    if not used_llm:
        papers.sort(key=score_paper, reverse=True)
        top = papers[:3]
        for p in top:
            p["summary_english"] = p["summary"][:600]
    print(f"[INFO] Top {len(top)} papers:")
    for p in top:
        print(f"  [{p.get('primary_category','?')}] {p['title'][:80]}")
    html = build_html(top, used_llm)
    send_email(html)
    print(f"[{datetime.datetime.now().isoformat()}] === DONE ===")

if __name__ == "__main__":
    main()
