"""
Waseda Moodle Task Enforcer — iCal経由で課題取得・DeepSeekの叱咤激励付きメール送信。
"""
import os, sys, json, datetime, smtplib, urllib.request, urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from icalendar import Calendar
from typing import List, Dict

ICS_URL        = os.environ.get("MOODLE_ICAL_URL", "").strip()
GMAIL_ADDRESS  = os.environ.get("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PW   = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
LLM_API_KEY    = os.environ.get("LLM_API_KEY", "").strip()
LLM_API_URL    = "https://api.deepseek.com/v1/chat/completions"
LLM_MODEL      = "deepseek-chat"
LOOKAHEAD_DAYS = 7
MAX_TASKS      = 15

def fetch_tasks(url: str) -> List[Dict]:
    if not url:
        print("[ERROR] MOODLE_ICAL_URL is empty.")
        return []
    req = urllib.request.Request(url, headers={"User-Agent": "MoodleEnforcer/1.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.URLError as e:
        print(f"[FATAL] Cannot fetch iCal: {e}")
        return []
    cal = Calendar.from_ical(resp.read())
    now = datetime.datetime.now(datetime.timezone.utc)
    limit = now + datetime.timedelta(days=LOOKAHEAD_DAYS)
    tasks = []
    for comp in cal.walk():
        if comp.name != "VEVENT":
            continue
        summary = str(comp.get("summary", "（課題名不明）"))
        dtstart = comp.get("dtstart")
        if dtstart is None:
            continue
        dt = dtstart.dt
        if isinstance(dt, datetime.date) and not isinstance(dt, datetime.datetime):
            dt = datetime.datetime.combine(dt, datetime.time.min, tzinfo=datetime.timezone.utc)
        elif isinstance(dt, datetime.datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
        else:
            continue
        if now <= dt <= limit:
            tasks.append(dict(
                title=summary,
                deadline_utc=dt,
                deadline_jst=dt.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
            ))
    tasks.sort(key=lambda t: t["deadline_utc"])
    return tasks[:MAX_TASKS]

def generate_message(tasks: List[Dict]) -> str:
    if not tasks:
        return ("🎉 直近7日間の課題はありません。"
                "この静寂を好機と捉え、作用素環論・自由確率論・ランダム行列理論の研究に"
                "全認知リソースを投下してください。ベンチプレスも忘れずに。")

    task_lines = []
    for t in tasks:
        jst = t["deadline_jst"].strftime("%m/%d(%a) %H:%M")
        task_lines.append(f"- {jst}: {t['title']}")
    task_str = "\n".join(task_lines)

    if not LLM_API_KEY:
        return f"⚠️ 直近7日間に {len(tasks)} 件の課題があります。\n\n{task_str}\n\n最優先タスクから即座に処理してください。"

    prompt = f"""あなたは冷徹で合理的な数学メンターです。私は2028年秋に海外トップスクールで
数学PhD（作用素環論・自由確率論・ランダム行列理論）取得を目指す早稲田大学3年生です。
以下のMoodle課題リストを確認し、これらを爆速で片付けて本来の数学研究に戻るよう、
論理的かつ厳しく急かすメッセージを日本語で200字程度で書いてください。

課題リスト:
{task_str}

条件:
- 「早くやれ」だけでなく「なぜ今すぐやるべきか」を数学者らしい論理で述べる
- 文体は冷徹だがユーモアもわずかに含める
- 200字程度"""
    try:
        body = json.dumps({"model": LLM_MODEL, "messages": [{"role":"user","content":prompt}],
                           "temperature":0.8, "max_tokens":500})
        req = urllib.request.Request(LLM_API_URL, data=body.encode(),
                                     headers={"Content-Type":"application/json",
                                              "Authorization":f"Bearer {LLM_API_KEY}"})
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[WARN] LLM failed: {e}")
        return f"⚠️ 直近7日間に {len(tasks)} 件の課題があります。今すぐ着手してください。\n\n{task_str}"

def send_email(tasks: List[Dict], message: str):
    if not GMAIL_ADDRESS or not GMAIL_APP_PW:
        print("[SKIP] Gmail secrets not set.")
        return
    today = datetime.date.today().strftime("%m/%d(%a)")
    count = len(tasks)
    task_html = ""
    if tasks:
        for t in tasks:
            jst = t["deadline_jst"].strftime("%m/%d(%a) %H:%M")
            task_html += f"<li><b>{jst}</b>　{t['title']}</li>"
    else:
        task_html = "<li>なし 🎉</li>"

    html = f"""<html><body style="font-family:Meiryo,Hiragino Sans,sans-serif">
<h2>🤖 Moodleタスク強制執行システム</h2>
<p style="color:#888;">{today} 時点｜直近7日間の課題数: {count}件</p>
<h3>📢 本日のメッセージ</h3>
<blockquote style="border-left:4px solid #d9534f;padding-left:12px;color:#444;">
{message.replace(chr(10),'<br>')}
</blockquote>
<hr>
<h3>📝 迫り来る課題リスト</h3>
<ul>{task_html}</ul>
<hr>
<p style="font-size:small;color:#aaa;">Waseda Moodle iCal → DeepSeek → Gmail｜GitHub Actions</p>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Moodle課題 ({count}件) — {today}"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = GMAIL_ADDRESS
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
            s.sendmail(GMAIL_ADDRESS, [GMAIL_ADDRESS], msg.as_string())
            s.quit()
            print(f"[OK] Email sent via {method}:{port}")
            return
        except smtplib.SMTPAuthenticationError:
            print("[FATAL] Gmail auth rejected. Check GMAIL_APP_PASSWORD.")
            sys.exit(1)
        except Exception as e:
            errors.append(f"{method}:{port} → {e}")
    print(f"[ERROR] All SMTP methods failed: {errors}")
    sys.exit(1)

def main():
    print(f"[{datetime.datetime.now().isoformat()}] === MOODLE ENFORCER START ===")
    if not ICS_URL:
        print("[ERROR] MOODLE_ICAL_URL is not set.")
        sys.exit(1)
    tasks = fetch_tasks(ICS_URL)
    print(f"[INFO] Found {len(tasks)} upcoming task(s)")
    for t in tasks:
        print(f"  {t['deadline_jst'].strftime('%m/%d %H:%M')} | {t['title'][:60]}")
    msg = generate_message(tasks)
    print(f"[INFO] Message: {msg[:120]}...")
    send_email(tasks, msg)
    print(f"[{datetime.datetime.now().isoformat()}] === DONE ===")

if __name__ == "__main__":
    main()
