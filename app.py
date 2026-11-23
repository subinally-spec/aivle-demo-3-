
import os
import sys
import warnings
import base64
import hashlib
import requests
import re
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
from openai import OpenAI

st.set_page_config(page_title="에이블", page_icon="🤖", layout="wide")

warnings.filterwarnings(action="ignore")

# -----------------------------
# API KEY 및 경로 설정  (★ 살리기 버전: 여기만 크게 바뀜)
# -----------------------------
# 키는 아래 순서로 찾습니다.
#   1) Hugging Face Space > Settings > Secrets 에 등록한 값 (환경변수)
#   2) 같은 폴더의 api_key.txt  (예: GROQ_API_KEY=gsk_xxx)
#
# LLM_PROVIDER 로 어떤 회사 API를 쓸지 고릅니다.
#   - groq   : 무료(카드 불필요). 채팅 + 음성인식 모두 가능  ← 추천
#   - gemini : 무료(구글 AI Studio 키). 채팅만, 음성인식 X
#   - openai : 유료(본인 결제 키). 원래 에이블 때와 똑같이 동작
# LLM_PROVIDER 를 안 넣으면, 등록된 키를 보고 자동으로 고릅니다.
PROJECT_DIR = "/content/drive/MyDrive/project03"
path = PROJECT_DIR if os.path.exists(PROJECT_DIR) else os.getcwd()
filepath = os.path.join(path, "api_key.txt")

if os.path.exists(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

PROVIDERS = {
    "groq": {
        "key_env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "chat_model": "openai/gpt-oss-120b",
        "stt_model": "whisper-large-v3-turbo",
    },
    "gemini": {
        "key_env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "chat_model": "gemini-flash-latest",
        "stt_model": None,
    },
    "openai": {
        "key_env": "OPENAI_API_KEY",
        "base_url": None,
        "chat_model": "gpt-4o-mini",
        "stt_model": "whisper-1",
    },
}

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "").strip().lower()
if LLM_PROVIDER not in PROVIDERS:
    LLM_PROVIDER = next(
        (p for p in ["groq", "gemini", "openai"] if os.environ.get(PROVIDERS[p]["key_env"])),
        "groq",
    )

_cfg = PROVIDERS[LLM_PROVIDER]
API_KEY = os.environ.get(_cfg["key_env"], "")
CHAT_MODEL = os.environ.get("LLM_MODEL") or _cfg["chat_model"]  # 모델 바꾸려면 Secrets에 LLM_MODEL 추가
STT_MODEL = _cfg["stt_model"]

client = OpenAI(api_key=API_KEY or "missing-key", base_url=_cfg["base_url"])


def ask_llm(messages):
    """원래 코드의 client.responses.create(...) 대신 쓰는 공통 함수.
    responses API는 OpenAI 전용이라, 모든 회사가 지원하는 chat.completions 로 통일."""
    if not API_KEY:
        raise RuntimeError(
            f"{_cfg['key_env']} 가 설정되지 않았습니다. Hugging Face Space > Settings > Secrets 에 등록해 주세요."
        )
    resp = client.chat.completions.create(model=CHAT_MODEL, messages=messages)
    return resp.choices[0].message.content or ""

# -----------------------------
# 사용자 정보 및 세션 설정
# -----------------------------
USERS = {
    "d091157": {
        "name": "공룡",
        "label": "공룡 회장",
        "aliases": ["공룡", "공룡 회장", "공룡 회장님", "회장님"]
    },
    "d091019": {
        "name": "김상민",
        "label": "김상민 상무",
        "aliases": ["김상민", "김상민 상무", "김 상무", "김상민 상무님", "김 상무님", "상무님", "상무", "상민", "상민 씨"]
    },
     "d091143": {
        "name": "정수빈",
        "label": "정수빈 사원",
        "aliases": ["정수빈", "정수빈 사원", "정 사원", "정사원", "수빈 사원", "수빈님", "수빈", "수빈 씨"]
    },
    "d091217": {
        "name": "최다희",
        "label": "최다희 팀장",
        "aliases": ["최다희", "최다희 팀장", "최 팀장", "최팀장", "다희 팀장", "다희님", "팀장님", "다희", "다희 씨"]
    }
}

# 세션 초기화 (분석기용 상태변수 추가)
for key, val in {
    "page": "login", "employee_id": "", "menu": "홈",
    "user_info": None, "todos": [],
    "messages": [{"role": "assistant", "content": "안녕하세요. AI 비서 AIVLE입니다. 무엇을 도와드릴까요?"}],
    "file_text": "", "uploaded_file_name": None, "last_audio_hash": None,
    "file_uploader_key": 0, "audio_input_key": 0, "audio_file_uploader_key": 0,
    "copy_result": "",
    # 분석기 페이지용 상태
    "chatbot_logs": [], "chatbot_question": "", "chatbot_answer": "",
    "chatbot_summary": "", "chatbot_audio": None, "chatbot_mail_to": "",
    "chatbot_mail_subject": "", "chatbot_mail_body": ""
}.items():
    if key not in st.session_state:
        st.session_state[key] = val

# -----------------------------
# 이메일 발송 함수 (SendGrid 기반)
# -----------------------------
def send_email(to, subject, body):
    api_key = os.getenv("SENDGRID_API_KEY")
    sender = os.getenv("EMAIL_ADDRESS")

    if not api_key or not sender:
        return {"status": "failed", "error": "발송 설정(SENDGRID_API_KEY 또는 EMAIL_ADDRESS)이 환경 변수에 설정되지 않았습니다."}

    url = "https://api.sendgrid.com/v3/mail/send"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    data = {
        "personalizations": [{"to": [{"email": to}], "subject": subject}],
        "from": {"email": sender},
        "content": [{"type": "text/plain", "value": body}]
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        if response.status_code == 202:
            return {"status": "success"}
        else:
            return {"status": "failed", "error": response.text}
    except Exception as e:
        return {"status": "failed", "error": str(e)}

# -----------------------------
# 유틸리티 함수 (날씨/식단/음성처리)
# -----------------------------
def get_weather_text(code):
    weather_map = {
        0: "맑음", 1: "대체로 맑음", 2: "부분적으로 흐림", 3: "흐림",
        45: "안개", 48: "서리 안개", 51: "약한 이슬비", 53: "이슬비",
        55: "강한 이슬비", 61: "약한 비", 63: "비", 65: "강한 비",
        71: "약한 눈", 73: "눈", 75: "강한 눈", 80: "약한 소나기",
        81: "소나기", 82: "강한 소나기", 95: "천둥번개"
    }
    return weather_map.get(code, "날씨 정보 없음")

@st.cache_data(ttl=600)
def get_jeonnong_weather():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": 37.58, "longitude": 127.05,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "timezone": "Asia/Seoul"
    }
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    current = response.json()["current"]
    return {
        "temperature": current.get("temperature_2m"),
        "apparent_temperature": current.get("apparent_temperature"),
        "humidity": current.get("relative_humidity_2m"),
        "precipitation": current.get("precipitation"),
        "weather": get_weather_text(current.get("weather_code")),
        "wind_speed": current.get("wind_speed_10m")
    }

def transcribe_audio(audio_file, filename="audio.wav", mime_type="audio/wav"):
    audio_bytes = audio_file.getvalue()
    if not STT_MODEL:
        raise RuntimeError(f"현재 선택한 API({LLM_PROVIDER})는 음성인식을 지원하지 않습니다. 음성 기능은 groq 또는 openai 키로 사용하세요.")
    transcript = client.audio.transcriptions.create(
        model=STT_MODEL,
        file=(filename, audio_bytes, mime_type)
    )
    return transcript.text

def create_answer_audio(answer_text):
    audio_path = os.path.join(path, "chatbot_answer.mp3")
    tts_text = answer_text[:3500]
    try:
        with client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=tts_text,
            response_format="mp3"
        ) as response:
            response.stream_to_file(audio_path)
    except Exception:
        with client.audio.speech.with_streaming_response.create(
            model="tts-1",
            voice="alloy",
            input=tts_text,
            response_format="mp3"
        ) as response:
            response.stream_to_file(audio_path)

    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    return audio_bytes

def render_audio_autoplay(audio_bytes):
    audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
    st.markdown(
        f"""
        <audio controls autoplay style="width: 100%;">
            <source src="data:audio/mp3;base64,{audio_base64}" type="audio/mp3">
        </audio>
        """,
        unsafe_allow_html=True
    )

def extract_individual_todos(text):
    extracted = []
    lines = text.split("\n")
    for line in lines:
        cleaned = line.strip()
        if re.match(r"^[-*+]\s+", cleaned) or re.match(r"^\d+\.\s+", cleaned):
            todo_item = re.sub(r"^[-*+]\s+", "", cleaned)
            todo_item = re.sub(r"^\d+\.\s+", "", todo_item)
            todo_item = todo_item.replace("**", "").strip()
            if todo_item:
                extracted.append(todo_item)
    if not extracted:
        for line in lines:
            cleaned = line.strip()
            if len(cleaned) > 5 and not cleaned.startswith("#") and not cleaned.startswith("["):
                extracted.append(cleaned)
    return extracted

# -----------------------------
# 스티커 메모 데이터 처리 함수
# -----------------------------
MEMO_FILE = os.path.join(path, "memos.json")

def load_memos():
    if not os.path.exists(MEMO_FILE):
        return {}
    try:
        with open(MEMO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_memos(memos):
    with open(MEMO_FILE, "w", encoding="utf-8") as f:
        json.dump(memos, f, ensure_ascii=False, indent=2)

# -----------------------------
# 위젯 렌더링 함수
# -----------------------------
def render_home_widgets():
    today = datetime.now(ZoneInfo("Asia/Seoul"))
    st.markdown("### 오늘의 정보")
    card1, card2 = st.columns(2)

    with card1:
        st.markdown(f"""
            <div style="padding: 24px; border-radius: 20px; background-color: #f5f6f8; min-height: 170px;">
                <div style="font-size: 18px; color: #666;">오늘 날짜</div>
                <div style="font-size: 30px; font-weight: 700; margin-top: 12px;">{today.strftime("%Y년 %m월 %d일")}</div>
                <div style="font-size: 18px; margin-top: 8px;">{today.strftime("%A")}</div>
            </div>
        """, unsafe_allow_html=True)

    with card2:
        try:
            w = get_jeonnong_weather()
            st.markdown(f"""
                <div style="padding: 24px; border-radius: 20px; background-color: #f5f6f8; min-height: 170px;">
                    <div style="font-size: 18px; color: #666;">서울 전농동 날씨</div>
                    <div style="font-size: 30px; font-weight: 700; margin-top: 12px;">{w["temperature"]}°C · {w["weather"]}</div>
                    <div style="font-size: 16px; margin-top: 8px; color: #555;">체감 {w["apparent_temperature"]}°C · 습도 {w["humidity"]}%</div>
                    <div style="font-size: 16px; margin-top: 4px; color: #555;">강수 {w["precipitation"]}mm · 풍속 {w["wind_speed"]}km/h</div>
                </div>
            """, unsafe_allow_html=True)
        except:
            st.warning("날씨 정보를 불러오지 못했습니다.")

def render_today_menu_widget():
    menu = {
        "메인": "닭갈비 덮밥", "국": "미소장국",
        "반찬": ["양배추 샐러드", "단무지", "김치"], "디저트": "요거트",
        "추천": "점심 식사 후, 목표하신 OPIC IH 달성을 위해 영어 스크립트를 가볍게 훑어보시는 건 어떨까요?"
    }
    st.markdown("### 오늘의 메뉴")
    st.markdown(f"""
        <div style="padding: 24px; border-radius: 20px; background-color: #f5f6f8; margin-top: 12px; margin-bottom: 24px;">
            <div style="font-size: 18px; color: #666;">사내 식단</div>
            <div style="font-size: 28px; font-weight: 700; margin-top: 10px;">{menu["메인"]}</div>
            <div style="font-size: 16px; color: #555; margin-top: 8px;">국: {menu["국"]}</div>
            <div style="font-size: 16px; color: #555; margin-top: 4px;">반찬: {", ".join(menu["반찬"])}</div>
            <div style="font-size: 16px; color: #555; margin-top: 4px;">디저트: {menu["디저트"]}</div>
            <div style="font-size: 15px; color: #007bff; margin-top: 14px; font-weight: 500;">💡 {menu["추천"]}</div>
        </div>
    """, unsafe_allow_html=True)

def render_sticky_memo_widget(user_info):
    user_key = st.session_state.get("login_employee_id", "")
    if not user_key:
        user_key = st.session_state.get("employee_id", "").strip().lower()
    if not user_key:
        user_key = user_info.get("label", "default_user")

    memos = load_memos()

    if user_key in memos and isinstance(memos[user_key], str):
        old_memo = memos[user_key].strip()
        if old_memo:
            memos[user_key] = [{"id": "old_memo", "text": old_memo, "created_at": "이전 저장 메모"}]
        else:
            memos[user_key] = []
        save_memos(memos)

    if user_key not in memos:
        memos[user_key] = []

    st.markdown("---")
    st.markdown("### 📝 스티커 메모")

    write_col, saved_col = st.columns([2, 1], gap="large")

    with write_col:
        st.markdown("#### 새 메모 작성")
        st.markdown("""<div style="padding: 12px 14px; border-radius: 12px 12px 0 0; background-color: #fff4b8; border: 1px solid #f0dd75; font-size: 15px; font-weight: 700;">개인 메모</div>""", unsafe_allow_html=True)
        with st.form(f"memo_form_{user_key}", clear_on_submit=True):
            memo_text = st.text_area("메모 내용", height=160, label_visibility="collapsed", placeholder="오늘 기억할 내용, 아이디어, 회의 메모 등을 적어두세요.")
            submitted = st.form_submit_button("메모 저장", use_container_width=True)

        if submitted:
            memo_text = memo_text.strip()
            if not memo_text:
                st.warning("저장할 메모를 입력해주세요.")
            else:
                now = datetime.now(ZoneInfo("Asia/Seoul"))
                new_memo = {"id": now.strftime("%Y%m%d%H%M%S%f"), "text": memo_text, "created_at": now.strftime("%Y-%m-%d %H:%M")}
                memos = load_memos()
                if user_key not in memos or not isinstance(memos[user_key], list):
                    memos[user_key] = []
                memos[user_key].insert(0, new_memo)
                save_memos(memos)
                st.success("메모가 저장되었습니다.")
                st.rerun()

    with saved_col:
        st.markdown("##### 저장된 메모")
        st.caption("수정은 불가능하고 삭제만 가능합니다.")
        user_memos = memos.get(user_key, [])
        if not user_memos:
            st.info("저장된 메모가 없습니다.")

        for idx, memo in enumerate(user_memos):
            if isinstance(memo, str):
                memo_id, memo_text, created_at = f"old_memo_{idx}", memo, "이전 저장 메모"
            else:
                memo_id, memo_text, created_at = memo.get("id", f"memo_{idx}"), memo.get("text", ""), memo.get("created_at", "저장 시간 없음")

            st.markdown(
                f"""
                <div style="
                    padding: 6px 10px;
                    border-radius: 10px;
                    background-color: #fff8c6;
                    border: 1px solid #e8d86b;
                    margin-bottom: 4px;
                    word-break: break-word;
                    white-space: pre-wrap;
                    display: flex;
                    flex-direction: column;
                    gap: 2px;
                ">
                    <div style="
                        font-size: 10px;
                        color: #7a7a7a;
                        line-height: 1.1;
                        margin: 0;
                    ">
                        {created_at}
                    </div>
                    <div style="
                        font-size: 12px;
                        color: #333;
                        line-height: 1.25;
                        margin: 0;
                    ">
                        {memo_text}
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )

            if st.button("삭제", key=f"delete_memo_{memo_id}"):
                memos = load_memos()
                memos[user_key] = [item for item in memos.get(user_key, []) if not (isinstance(item, dict) and item.get("id") == memo_id)]
                save_memos(memos)
                st.success("메모가 삭제되었습니다.")
                st.rerun()

# -----------------------------
# 페이지 렌더링 (로그인/메인/카피/분석기)
# -----------------------------
def go_personal_page():
    eid = st.session_state.employee_id.strip().lower()
    if eid in USERS:
        st.session_state.page, st.session_state.user_info = "personal", USERS[eid]
    else:
        st.error("등록되지 않은 사번입니다.")

def render_login_page():
    logo_url = "https://huggingface.co/spaces/major0404/streamlit/resolve/main/logo.png"
    st.markdown(f"""<div style="display:flex; justify-content:center; margin-top:80px; margin-bottom:10px;"><img src="{logo_url}" width="240" onerror="this.onerror=null; this.src='https://raw.githubusercontent.com/Jangrae/img/master/gen_ai_title.png';"></div>""", unsafe_allow_html=True)
    _, col, _ = st.columns([1, 1.5, 1])
    with col:
        st.text_input("사번", key="employee_id", placeholder="사번을 입력하세요", label_visibility="collapsed", on_change=go_personal_page)
        if st.button("입장하기", use_container_width=True):
            go_personal_page()

def render_copy_page(user_info):
    user_label = user_info["label"]
    st.title("✍️ 카피 생성기")
    st.write("마케팅 업무에 필요한 문구, 콘텐츠 초안, 분석 템플릿을 생성합니다.")
    copy_mode = st.selectbox("생성할 작업을 선택하세요.", ["SNS 홍보 콘텐츠 초안", "광고 문구", "인플루언서 섭외 DM", "경쟁사 마케팅 사례 분석 템플릿", "광고 예산 논의 초안"])
    service_name = st.text_input("상품/서비스명")
    target = st.text_input("타깃 고객", placeholder="예: 20대 직장인, 대학생, 예비 창업자")
    channel = st.selectbox("채널", ["인스타그램", "블로그", "유튜브", "검색광고", "랜딩페이지", "이메일"])
    tone = st.selectbox("톤앤매너", ["친근한", "전문적인", "감성적인", "트렌디한", "간결한", "고급스러운"])
    extra_info = st.text_area("추가 정보", placeholder="캠페인 목적, 핵심 메시지, 경쟁사, 예산, 일정 등을 자유롭게 입력하세요.", height=140)

    if st.button("생성하기", use_container_width=True):
        if not service_name and not extra_info:
            st.warning("상품/서비스명 또는 추가 정보를 입력해주세요.")
            return

        sys_role = f"당신은 마케팅 회사의 AI 업무 비서 에이블입니다.\n사용자는 {user_label}입니다.\n\n아래 조건에 맞게 마케팅 실무에 바로 사용할 수 있는 결과물을 작성하세요.\n불필요한 설명은 줄이고, 복사해서 바로 쓸 수 있는 형태로 작성하세요."
        prompt = f"[작업 유형]\n{copy_mode}\n\n[상품/서비스명]\n{service_name}\n\n[타깃 고객]\n{target}\n\n[채널]\n{channel}\n\n[톤앤매너]\n{tone}\n\n[추가 정보]\n{extra_info}\n\n[출력 요청]\n- 실무자가 바로 복사해 사용할 수 있게 작성\n- 제목과 본문을 구분\n- 필요한 경우 해시태그, CTA, 체크리스트 포함\n- 마크다운 형식으로 출력"

        with st.spinner("마케팅 초안 및 카피를 생성하고 있습니다..."):
            try:
                st.session_state.copy_result = ask_llm(
                    [{"role": "system", "content": sys_role}, {"role": "user", "content": prompt}]
                )
            except Exception as e:
                st.error(f"오류가 발생했습니다: {e}")

    if "copy_result" in st.session_state and st.session_state.copy_result:
        st.markdown("---")
        st.markdown("## 📋 생성 결과")
        st.markdown(st.session_state.copy_result)
        st.markdown("### 💾 복사용 텍스트 영역")
        st.text_area("아래 내용을 복사해서 사용하세요.", value=st.session_state.copy_result, height=320)
        st.download_button(label="📥 마크다운 파일로 다운로드", data=st.session_state.copy_result, file_name="marketing_copy.md", mime="text/markdown", use_container_width=True)

def render_analyzer_page(user_info):
    import pandas as pd
    import numpy as np
    from io import BytesIO
    from datetime import datetime

    user_label = user_info["label"]

    st.title("🔎 분석기")
    st.write("CSV 파일을 업로드하면 마케팅 핵심 지표를 계산하고, 시각화와 보고서를 생성합니다.")

    if "analyzer_report" not in st.session_state:
        st.session_state.analyzer_report = ""

    if "analyzer_file_name" not in st.session_state:
        st.session_state.analyzer_file_name = ""

    def read_csv_safely(uploaded_file):
        raw = uploaded_file.getvalue()

        for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
            try:
                return pd.read_csv(BytesIO(raw), encoding=enc)
            except Exception:
                pass

        return pd.read_csv(BytesIO(raw))

    def find_col(columns, keywords):
        for col in columns:
            normalized = str(col).lower().replace(" ", "").replace("_", "")
            for keyword in keywords:
                key = keyword.lower().replace(" ", "").replace("_", "")
                if key in normalized:
                    return col
        return None

    def to_number(series):
        return pd.to_numeric(
            series.astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("₩", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.replace("원", "", regex=False)
            .str.strip(),
            errors="coerce"
        ).fillna(0)

    def safe_div(a, b):
        if b is None or b == 0:
            return 0
        return a / b

    def money(x):
        try:
            return f"{int(round(x)):,}원"
        except Exception:
            return "-"

    def num(x):
        try:
            return f"{int(round(x)):,}"
        except Exception:
            return "-"

    def pct(x):
        try:
            return f"{x:.2f}%"
        except Exception:
            return "-"

    def build_basic_report(file_name, total, top_table, weak_table, has_revenue):
        today = datetime.now().strftime("%Y-%m-%d")

        report = f"""# 마케팅 성과 분석 리포트

작성일: {today}
분석 파일: {file_name}
작성자: AI 비서 에이블

## 1. 핵심 요약

- 총 노출수: {num(total.get("impressions", 0))}
- 총 클릭수: {num(total.get("clicks", 0))}
- 총 광고비: {money(total.get("cost", 0))}
- 총 전환수: {num(total.get("conversions", 0))}
- CTR: {pct(total.get("ctr", 0))}
- CPC: {money(total.get("cpc", 0))}
- CVR: {pct(total.get("cvr", 0))}
- CPA: {money(total.get("cpa", 0))}
"""

        if has_revenue:
            report += f"- 총 매출: {money(total.get('revenue', 0))}\n"
            report += f"- ROAS: {pct(total.get('roas', 0))}\n"

        report += """

## 2. 성과가 좋은 항목

"""

        if top_table.empty:
            report += "- 성과가 좋은 항목을 판단하기 위한 데이터가 부족합니다.\n"
        else:
            for _, row in top_table.head(3).iterrows():
                report += f"- {row['분석 기준']}: 클릭 {num(row.get('clicks', 0))}, 전환 {num(row.get('conversions', 0))}, 광고비 {money(row.get('cost', 0))}, CTR {pct(row.get('CTR', 0))}, CPA {money(row.get('CPA', 0))}\n"
                if has_revenue and "ROAS" in row:
                    report += f"  - ROAS: {pct(row.get('ROAS', 0))}\n"

        report += """

## 3. 점검이 필요한 항목

"""

        if weak_table.empty:
            report += "- 점검이 필요한 항목을 판단하기 위한 데이터가 부족합니다.\n"
        else:
            for _, row in weak_table.head(3).iterrows():
                report += f"- {row['분석 기준']}: 광고비 {money(row.get('cost', 0))}, 클릭 {num(row.get('clicks', 0))}, 전환 {num(row.get('conversions', 0))}, CPA {money(row.get('CPA', 0))}\n"

        report += """

## 4. 실행 제안

- 전환이 높고 CPA가 낮은 항목은 예산 증액을 검토합니다.
- 광고비는 높지만 전환이 낮은 항목은 소재, 타깃, 랜딩페이지를 우선 점검합니다.
- CTR이 낮은 항목은 광고 문구와 썸네일, 후킹 메시지를 개선합니다.
- 클릭은 충분하지만 CVR이 낮은 항목은 랜딩페이지 설득력과 전환 흐름을 점검합니다.
- 다음 보고 시에는 캠페인별 예산, 클릭, 전환, 매출을 같은 기준으로 관리하는 것이 좋습니다.
"""

        return report

    uploaded_csv = st.file_uploader(
        "마케팅 성과 CSV 파일 업로드",
        type=["csv"]
    )

    if uploaded_csv is None:
        st.info("CSV 파일을 업로드하면 분석이 시작됩니다.")
        st.caption("권장 컬럼 예시: campaign, channel, impressions, clicks, cost, conversions, revenue")
        return

    try:
        df = read_csv_safely(uploaded_csv)
    except Exception as e:
        st.error("CSV 파일을 읽지 못했습니다.")
        st.code(str(e))
        return

    st.session_state.analyzer_file_name = uploaded_csv.name

    if df.empty:
        st.warning("CSV 파일에 데이터가 없습니다.")
        return

    st.markdown("### 1. 데이터 미리보기")
    st.dataframe(df.head(20), use_container_width=True)

    columns = list(df.columns)

    auto_dimension = find_col(
        columns,
        ["campaign", "campaign_name", "캠페인", "광고명", "adset", "ad group", "채널", "channel", "source", "매체"]
    )
    auto_impressions = find_col(columns, ["impressions", "impression", "노출", "노출수", "imp"])
    auto_clicks = find_col(columns, ["clicks", "click", "클릭", "클릭수"])
    auto_cost = find_col(columns, ["cost", "spend", "amount spent", "광고비", "비용", "지출", "집행금액"])
    auto_conversions = find_col(columns, ["conversions", "conversion", "전환", "전환수", "구매", "신청", "가입", "lead"])
    auto_revenue = find_col(columns, ["revenue", "sales", "매출", "수익", "전환값", "전환가치", "구매금액", "purchase value"])
    auto_date = find_col(columns, ["date", "날짜", "일자", "day"])

    none_option = "선택 안 함"
    options = [none_option] + columns

    def selected_index(auto_col):
        if auto_col in columns:
            return options.index(auto_col)
        return 0

    with st.expander("컬럼 매핑 확인 / 수정", expanded=True):
        map_col1, map_col2, map_col3 = st.columns(3)

        with map_col1:
            dimension_col = st.selectbox(
                "분석 기준 컬럼",
                options,
                index=selected_index(auto_dimension)
            )
            impressions_col = st.selectbox(
                "노출수 컬럼",
                options,
                index=selected_index(auto_impressions)
            )

        with map_col2:
            clicks_col = st.selectbox(
                "클릭수 컬럼",
                options,
                index=selected_index(auto_clicks)
            )
            cost_col = st.selectbox(
                "광고비 컬럼",
                options,
                index=selected_index(auto_cost)
            )

        with map_col3:
            conversions_col = st.selectbox(
                "전환수 컬럼",
                options,
                index=selected_index(auto_conversions)
            )
            revenue_col = st.selectbox(
                "매출 컬럼",
                options,
                index=selected_index(auto_revenue)
            )

    required_cols = [impressions_col, clicks_col, cost_col, conversions_col]

    if all(col == none_option for col in required_cols):
        st.warning("노출수, 클릭수, 광고비, 전환수 중 최소 1개 이상은 매핑되어야 분석할 수 있습니다.")
        return

    work_df = df.copy()

    if dimension_col == none_option:
        work_df["분석 기준"] = "전체"
        dimension_col = "분석 기준"

    metric_cols = {
        "impressions": impressions_col,
        "clicks": clicks_col,
        "cost": cost_col,
        "conversions": conversions_col,
        "revenue": revenue_col
    }

    for new_col, original_col in metric_cols.items():
        if original_col != none_option:
            work_df[new_col] = to_number(work_df[original_col])
        else:
            work_df[new_col] = 0

    group_df = (
        work_df
        .groupby(dimension_col, dropna=False)[["impressions", "clicks", "cost", "conversions", "revenue"]]
        .sum()
        .reset_index()
        .rename(columns={dimension_col: "분석 기준"})
    )

    group_df["CTR"] = np.where(group_df["impressions"] > 0, group_df["clicks"] / group_df["impressions"] * 100, 0)
    group_df["CPC"] = np.where(group_df["clicks"] > 0, group_df["cost"] / group_df["clicks"], 0)
    group_df["CVR"] = np.where(group_df["clicks"] > 0, group_df["conversions"] / group_df["clicks"] * 100, 0)
    group_df["CPA"] = np.where(group_df["conversions"] > 0, group_df["cost"] / group_df["conversions"], 0)
    group_df["ROAS"] = np.where(group_df["cost"] > 0, group_df["revenue"] / group_df["cost"] * 100, 0)

    total_impressions = group_df["impressions"].sum()
    total_clicks = group_df["clicks"].sum()
    total_cost = group_df["cost"].sum()
    total_conversions = group_df["conversions"].sum()
    total_revenue = group_df["revenue"].sum()

    total = {
        "impressions": total_impressions,
        "clicks": total_clicks,
        "cost": total_cost,
        "conversions": total_conversions,
        "revenue": total_revenue,
        "ctr": safe_div(total_clicks, total_impressions) * 100,
        "cpc": safe_div(total_cost, total_clicks),
        "cvr": safe_div(total_conversions, total_clicks) * 100,
        "cpa": safe_div(total_cost, total_conversions),
        "roas": safe_div(total_revenue, total_cost) * 100
    }

    has_revenue = revenue_col != none_option and total_revenue > 0

    st.markdown("### 2. 핵심 지표")

    metric_row1 = st.columns(4)
    metric_row1[0].metric("총 노출수", num(total["impressions"]))
    metric_row1[1].metric("총 클릭수", num(total["clicks"]))
    metric_row1[2].metric("총 광고비", money(total["cost"]))
    metric_row1[3].metric("총 전환수", num(total["conversions"]))

    metric_row2 = st.columns(4)
    metric_row2[0].metric("CTR", pct(total["ctr"]))
    metric_row2[1].metric("CPC", money(total["cpc"]))
    metric_row2[2].metric("CVR", pct(total["cvr"]))
    metric_row2[3].metric("CPA", money(total["cpa"]))

    if has_revenue:
        metric_row3 = st.columns(2)
        metric_row3[0].metric("총 매출", money(total["revenue"]))
        metric_row3[1].metric("ROAS", pct(total["roas"]))

    st.markdown("### 3. 분석 테이블")

    display_df = group_df.copy()
    display_df["impressions"] = display_df["impressions"].round(0).astype(int)
    display_df["clicks"] = display_df["clicks"].round(0).astype(int)
    display_df["cost"] = display_df["cost"].round(0).astype(int)
    display_df["conversions"] = display_df["conversions"].round(0).astype(int)
    display_df["revenue"] = display_df["revenue"].round(0).astype(int)
    display_df["CTR"] = display_df["CTR"].round(2)
    display_df["CPC"] = display_df["CPC"].round(0)
    display_df["CVR"] = display_df["CVR"].round(2)
    display_df["CPA"] = display_df["CPA"].round(0)
    display_df["ROAS"] = display_df["ROAS"].round(2)

    st.dataframe(display_df, use_container_width=True)

    st.markdown("### 4. 시각화")

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        chart_metric = st.selectbox(
            "시각화 지표",
            ["cost", "clicks", "conversions", "CTR", "CVR", "CPA", "ROAS"]
        )

        chart_df = group_df.sort_values(chart_metric, ascending=False).head(10)
        st.bar_chart(
            chart_df.set_index("분석 기준")[[chart_metric]]
        )

    with chart_col2:
        st.markdown("#### 성과 상위 항목")

        if has_revenue:
            top_df = group_df[group_df["cost"] > 0].sort_values("ROAS", ascending=False).head(5)
            st.caption("ROAS 기준")
        else:
            top_df = group_df[group_df["conversions"] > 0].sort_values("CPA", ascending=True).head(5)
            st.caption("CPA 기준")

        st.dataframe(top_df, use_container_width=True)

    weak_df = group_df.sort_values(["cost", "conversions"], ascending=[False, True]).head(5)

    st.markdown("### 5. 보고서 생성")

    basic_report = build_basic_report(
        uploaded_csv.name,
        total,
        top_df,
        weak_df,
        has_revenue
    )

    if st.button("AI 보고서 생성", use_container_width=True):
        table_for_ai = display_df.head(20).to_csv(index=False)

        prompt = f"""
아래 마케팅 성과 데이터를 바탕으로 실무 보고서 형태의 마케팅 성과 분석 리포트를 작성하세요.

조건:
- 한국어로 작성
- 단순 수치 나열이 아니라 마케팅 관점의 해석 포함
- 성과가 좋은 항목, 개선이 필요한 항목, 다음 액션을 구분
- 상사에게 공유 가능한 보고서 문체
- 마크다운 형식
- 과도한 추정 금지
- 데이터에 없는 사실은 만들지 말 것

[기본 계산 리포트]
{basic_report}

[분석 테이블 CSV]
{table_for_ai}
"""

        with st.spinner("AI가 보고서를 생성하고 있습니다..."):
            try:
                st.session_state.analyzer_report = ask_llm(
                    [
                        {
                            "role": "system",
                            "content": "너는 마케팅 데이터 분석가입니다. 업로드된 성과 데이터를 기반으로 보고서를 작성합니다."
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ]
                )

            except Exception as e:
                st.warning("AI 보고서 생성에 실패해 기본 계산 리포트를 표시합니다.")
                st.code(str(e))
                st.session_state.analyzer_report = basic_report

    if not st.session_state.analyzer_report:
        st.session_state.analyzer_report = basic_report

    st.markdown("### 6. 보고서 미리보기")
    st.markdown(st.session_state.analyzer_report)

    report_file_name = f"marketing_report_{datetime.now().strftime('%Y%m%d_%H%M')}.md"

    st.download_button(
        label="📥 보고서 다운로드",
        data=st.session_state.analyzer_report,
        file_name=report_file_name,
        mime="text/markdown",
        use_container_width=True
    )

    st.markdown("### 7. 보고서 메일 작성 / 발송")

    with st.form("analyzer_email_form"):
        mail_to = st.text_input(
            "받는 사람 이메일",
            placeholder="example@company.com"
        )

        mail_subject = st.text_input(
            "메일 제목",
            value=f"[분석 리포트] {uploaded_csv.name}"
        )

        mail_body = st.text_area(
            "메일 본문",
            value=st.session_state.analyzer_report,
            height=320
        )

        send_submit = st.form_submit_button(
            "메일 발송",
            use_container_width=True
        )

    if send_submit:
        if not mail_to.strip():
            st.warning("받는 사람 이메일을 입력해주세요.")
        elif not mail_subject.strip():
            st.warning("메일 제목을 입력해주세요.")
        elif not mail_body.strip():
            st.warning("메일 본문을 입력해주세요.")
        else:
            result = send_email(
                to=mail_to.strip(),
                subject=mail_subject.strip(),
                body=mail_body.strip()
            )

            if result["status"] == "success":
                st.success("메일을 발송했습니다.")
            else:
                st.error("메일 발송에 실패했습니다.")
                st.code(result.get("error", "알 수 없는 오류"))

# -----------------------------
# 개인 대시보드 페이지 (통합 메인)
# -----------------------------
def render_personal_page():
    u = st.session_state.user_info

    st.sidebar.markdown("""
        <style>
        .sidebar-profile-card { background-color: #f8f9fa; padding: 20px; border-radius: 16px; border: 1px solid #e9ecef; text-align: center; margin-bottom: 25px; }
        .profile-avatar { font-size: 40px; margin-bottom: 8px; }
        .profile-name-text { font-size: 20px; font-weight: 700; color: #212529; }
        .profile-label-text { font-size: 13px; color: #6c757d; font-weight: 500; }
        .sidebar-section-title { font-size: 12px; font-weight: 700; color: #adb5bd; text-transform: uppercase; letter-spacing: 1.5px; margin-top: 20px; margin-bottom: 12px; padding-left: 5px; }
        div[data-testid="stSidebar"] div.stRadio { display: none; }
        </style>
    """, unsafe_allow_html=True)

    st.sidebar.markdown(f"""
        <div class="sidebar-profile-card">
            <div class="profile-avatar">👤</div>
            <div class="profile-name-text">{u['name']}님</div>
            <div class="profile-label-text">{u['label']}</div>
        </div>
    """, unsafe_allow_html=True)

    st.sidebar.markdown("<div class='sidebar-section-title'>MENU</div>", unsafe_allow_html=True)

    # 메뉴 이름 변경: 회의록 분석 -> 챗봇, 새로 추가: 분석기
    menus = [
        {"id": "홈", "icon": "🏠", "label": "홈"},
        {"id": "챗봇", "icon": "💬", "label": "챗봇"},  # (구)회의록 분석
        {"id": "분석기", "icon": "🔎", "label": "분석기"}, # 신규 추가
        {"id": "카피 생성기", "icon": "✍️", "label": "카피 생성기"},
        {"id": "할일", "icon": "✅", "label": "할일 목록"}
    ]

    for m in menus:
        is_active = (st.session_state.menu == m["id"])
        if st.sidebar.button(f"{m['icon']}  {m['label']}", key=f"menu_btn_{m['id']}", use_container_width=True, type="primary" if is_active else "secondary"):
            st.session_state.menu = m["id"]
            st.rerun()

    st.sidebar.markdown("---")
    if API_KEY:
        st.sidebar.caption(f"🔌 AI 연결: {LLM_PROVIDER} · {CHAT_MODEL}")
    else:
        st.sidebar.error(f"🔌 {_cfg['key_env']} 미설정 — Space Secrets에 키를 등록하세요.")
    st.sidebar.markdown("<div class='sidebar-section-title'>ACCOUNT</div>", unsafe_allow_html=True)
    if st.sidebar.button("🔓 시스템 로그아웃", use_container_width=True):
        st.session_state.page = "login"
        st.rerun()

    # --- 메인 페이지 분기 ---
    if st.session_state.menu == "홈":
        st.title(f"🏠 안녕하세요, {u['name']}님!")
        render_home_widgets()
        render_today_menu_widget()
        render_sticky_memo_widget(u)

    elif st.session_state.menu == "챗봇": # (구)회의록 분석 페이지
        st.title("💬 챗봇 (AI 비서 AIVLE)")
        st.caption("텍스트와 음성으로 대화하고, 업로드한 파일 내용을 참고해 답변합니다.")

        sys_role = f"당신은 다양한 업무를 처리하는 AI 비서 'AIVLE'입니다.\n현재 로그인한 사용자는 {u['label']} {u['name']}님입니다.\n사용자를 직급에 맞게 극진하고 친절하게 대하며, 요청에 정확하게 응대합니다.\n\n가능한 업무:\n- 회의록 분석 및 요약\n- 업무 지시사항 추출 (개별 할 일 목록으로 변환하여 제공)\n- To-Do List 생성\n- 질문 답변\n- 파일 내용 기반 답변\n- 음성 요청 처리\n"

        chat_col, side_col = st.columns([3, 1])
        with chat_col:
            st.markdown("## 💬 현재 대화")
            for message in st.session_state.messages:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

        with side_col:
            st.markdown("## ⚙️ 기능")
            uploaded_file = st.file_uploader("📁 텍스트 파일 업로드", type=["txt"], key=f"file_uploader_{st.session_state.file_uploader_key}")
            if uploaded_file is not None:
                st.session_state.file_text = uploaded_file.read().decode("utf-8")
                st.session_state.uploaded_file_name = uploaded_file.name
                st.success("텍스트 업로드 완료")
            if st.session_state.uploaded_file_name:
                st.caption(f"📎 {st.session_state.uploaded_file_name}")

            uploaded_audio_file = st.file_uploader("🎵 음성 파일 업로드", type=["mp3", "wav", "m4a", "ogg", "flac"], key=f"audio_file_uploader_{st.session_state.audio_file_uploader_key}")
            audio_value = st.audio_input("🎙️ 실시간 음성 질문", key=f"audio_input_{st.session_state.audio_input_key}")

            if st.button("🗑️ 대화 초기화", use_container_width=True):
                st.session_state.messages = [{"role": "assistant", "content": "대화가 초기화되었습니다. 무엇을 도와드릴까요?"}]
                st.session_state.file_text = ""
                st.session_state.uploaded_file_name = None
                st.session_state.last_audio_hash = None
                st.session_state.file_uploader_key += 1
                st.session_state.audio_input_key += 1
                st.session_state.audio_file_uploader_key += 1
                st.rerun()

        user_input = st.chat_input("메시지를 입력하세요...")
        voice_input = None
        target_audio_bytes = None

        if audio_value is not None:
            target_audio_bytes = audio_value.getvalue()
        elif uploaded_audio_file is not None:
            target_audio_bytes = uploaded_audio_file.read()

        if target_audio_bytes is not None:
            current_audio_hash = hashlib.md5(target_audio_bytes).hexdigest()
            if current_audio_hash != st.session_state.last_audio_hash:
                st.session_state.last_audio_hash = current_audio_hash
                with st.spinner("음성을 텍스트로 변환하고 있습니다..."):
                    try:
                        voice_input = transcribe_audio(audio_value if audio_value else uploaded_audio_file, filename="audio.wav", mime_type="audio/wav")
                    except Exception as e:
                        st.error(f"음성 변환 중 오류가 발생했습니다: {e}")
                st.session_state.audio_input_key += 1
                st.session_state.audio_file_uploader_key += 1

        final_user_input = user_input if user_input else voice_input

        if final_user_input:
            st.session_state.messages.append({"role": "user", "content": final_user_input})
            system_content = sys_role
            if st.session_state.file_text:
                system_content += f"\n\n[업로드된 파일 내용]\n{st.session_state.file_text}"

            with chat_col:
                with st.spinner("답변을 생성하고 있습니다..."):
                    try:
                        assistant_reply = ask_llm([{"role": "system", "content": system_content}] + st.session_state.messages)
                    except Exception as e:
                        assistant_reply = f"오류가 발생했습니다: {e}"

            st.session_state.messages.append({"role": "assistant", "content": assistant_reply})
            for item in extract_individual_todos(assistant_reply):
                if item not in st.session_state.todos:
                    st.session_state.todos.append(item)
            st.rerun()

    elif st.session_state.menu == "분석기":
        render_analyzer_page(u)

    elif st.session_state.menu == "카피 생성기":
        render_copy_page(u)

    elif st.session_state.menu == "할일":
      from datetime import timedelta

      if "todos_v2" not in st.session_state:
          st.session_state.todos_v2 = []

      if "todos_processed_raw" not in st.session_state:
          st.session_state.todos_processed_raw = []

      # ============================================================
      # 데이터 모델 / 마이그레이션 / 메타 부여 / 정렬 / UI
      # 기존 st.session_state.todos 문자열 리스트 -> todos_v2 dict 리스트
      # {id, text, owner, due_date, category, done, source}
      # ============================================================

      POSITION_KEYWORDS = [
          "회장", "사장", "부사장", "전무", "상무", "이사",
          "본부장", "팀장", "실장", "부장", "차장", "과장",
          "대리", "주임", "사원", "매니저", "리더", "PM", "PL"
      ]

      def clean_owner_text(value):
          value = str(value or "").strip()
          value = re.sub(r"\s+", "", value)
          value = value.replace("님", "").replace("씨", "")
          return value


      def normalize_owner_name(raw_owner):
          raw_clean = clean_owner_text(raw_owner)

          if not raw_clean:
              return "공용/미지정"

          for emp_id, info in USERS.items():
              candidates = [info.get("name", ""), info.get("label", "")]
              candidates += info.get("aliases", [])

              for c in candidates:
                  if clean_owner_text(c) == raw_clean:
                      return info["name"]

          # 김상민상무, 김은채대리처럼 붙은 형태도 처리
          for emp_id, info in USERS.items():
              name_clean = clean_owner_text(info.get("name", ""))
              label_clean = clean_owner_text(info.get("label", ""))

              if raw_clean == label_clean:
                  return info["name"]

              if name_clean and raw_clean.startswith(name_clean):
                  return info["name"]

          return raw_clean


      def build_owner_aliases():
          aliases = []

          for emp_id, info in USERS.items():
              aliases.append(info.get("name", ""))
              aliases.append(info.get("label", ""))
              aliases += info.get("aliases", [])

          aliases = [a for a in aliases if a]
          aliases = sorted(set(aliases), key=len, reverse=True)

          return aliases


      def strip_owner_prefix_from_task(text):
          text = str(text or "").strip()
          aliases = build_owner_aliases()

          for alias in aliases:
              pattern = re.escape(alias)
              text = re.sub(
                  rf"^{pattern}\s*(님|씨)?\s*(은|는|이|가|에게|께서|께서는)?\s*[:：\-—]?\s*",
                  "",
                  text
              ).strip()

          return text

      def detect_owner_in_text(text, current_user_name):
          text = str(text or "")

          aliases = build_owner_aliases()

          for alias in aliases:
              if alias and alias in text:
                  return normalize_owner_name(alias)

          position_pattern = "|".join(POSITION_KEYWORDS)

          owner_re = re.compile(
              rf"(?:^|[\s\*\(\[•·,])([가-힣]{{1,4}}\s*(?:{position_pattern}))"
          )

          for m in owner_re.finditer(text):
              full = m.group(1)
              return normalize_owner_name(full)

          name_re = re.compile(r"(?:^|[\s\*\(\[•·,])([가-힣]{2,4})(?:님|씨)")

          for m in name_re.finditer(text):
              return normalize_owner_name(m.group(1))

          return "공용/미지정"


      def split_into_atomic_items(raw_line):
          text = raw_line.replace("**", "").replace("__", "").strip()
          text = re.sub(r"^[-*+•·]\s*", "", text)
          text = re.sub(r"^\d+\.\s*", "", text)

          owner_prefix = None
          body = text

          aliases = build_owner_aliases()
          alias_pattern = "|".join([re.escape(a) for a in aliases if a])

          if alias_pattern:
              alias_re = re.compile(
                  rf"^({alias_pattern})\s*(님|씨)?\s*(은|는|이|가|에게|께서|께서는)?\s*[:：\-—]?\s*(.+)$"
              )

              m = alias_re.match(text)

              if m:
                  owner_prefix = normalize_owner_name(m.group(1))
                  body = m.group(4).strip()

          if owner_prefix is None:
              position_pattern = "|".join(POSITION_KEYWORDS)

              prefix_re = re.compile(
                  rf"^([가-힣]{{1,4}}\s*(?:{position_pattern}))\s*(은|는|이|가|에게|께서|께서는)?\s*[:：\-—]?\s*(.+)$"
              )

              m = prefix_re.match(text)

              if m:
                  owner_prefix = normalize_owner_name(m.group(1))
                  body = m.group(3).strip()

          body = strip_owner_prefix_from_task(body)

          parts = re.split(r"\s*[,，;；/]\s*|\s+및\s+|\s+그리고\s+", body)
          parts = [p.strip(" .。·•") for p in parts if p and p.strip()]

          if not parts:
              parts = [body]

          items = []

          for p in parts:
              p = strip_owner_prefix_from_task(p)

              if len(p) < 2:
                  continue

              items.append({
                  "text": p,
                  "owner_hint": owner_prefix
              })

          return items


      def enrich_with_gpt(items):
          if not items:
              return []

          today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")

          payload = [
              {
                  "idx": i,
                  "text": it["text"]
              }
              for i, it in enumerate(items)
          ]

          sys_msg = (
              "너는 회의에서 도출된 할 일들에 메타정보를 다는 비서다.\n"
              f"오늘은 {today} (Asia/Seoul) 이다.\n"
              "각 항목에 대해 다음을 추정하라:\n"
              "- due_date: YYYY-MM-DD 형식. 텍스트에 명시된 날짜나 표현이 있으면 계산해서 그 날짜로 작성.\n"
              "  예: 다음주 월요일, 이번주 금요일, 3일 내 등.\n"
              "  명시 안 됐으면 업무 성격을 보고 합리적 기본값을 지정. 보통 오늘+3~7일.\n"
              "  절대 null로 두지 마.\n"
              "- category: 1~6글자 한국어 짧은 태그. 예: 시장조사, 콘텐츠, 보고, 회의, 일정, 기획, 협업, 검토, 발송 등.\n"
              "출력은 반드시 JSON 한 개로만 작성.\n"
              "{\"items\":[{\"idx\":0,\"due_date\":\"YYYY-MM-DD\",\"category\":\"...\"}]} 형식.\n"
              "다른 말 금지. 코드블록 금지."
          )

          user_msg = json.dumps(
              {
                  "items": payload
              },
              ensure_ascii=False
          )

          try:
              raw = ask_llm(
                  [
                      {
                          "role": "system",
                          "content": sys_msg
                      },
                      {
                          "role": "user",
                          "content": user_msg
                      }
                  ]
              ).strip()
              raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
              parsed = json.loads(raw)

              meta_by_idx = {
                  m["idx"]: m
                  for m in parsed.get("items", [])
              }

          except Exception:
              meta_by_idx = {}

          user_name = u["name"]

          enriched = []

          for i, it in enumerate(items):
              meta = meta_by_idx.get(i, {})

              due = meta.get("due_date")

              if due:
                  try:
                      datetime.strptime(due, "%Y-%m-%d")
                  except Exception:
                      due = None

              if not due:
                  due = (
                      datetime.now(ZoneInfo("Asia/Seoul")) + timedelta(days=5)
                  ).strftime("%Y-%m-%d")

              category = (meta.get("category") or "기타").strip()[:8] or "기타"

              owner = it.get("owner_hint")

              if not owner:
                  owner = detect_owner_in_text(it["text"], user_name)

              enriched.append({
                  "text": it["text"],
                  "owner": owner,
                  "due_date": due,
                  "category": category
              })

          return enriched


      def migrate_new_todos():
          processed_set = set(st.session_state.todos_processed_raw)

          new_raws = [
              t for t in st.session_state.todos
              if t not in processed_set
          ]

          if not new_raws:
              return 0

          atoms = []

          for raw in new_raws:
              for atom in split_into_atomic_items(raw):
                  atoms.append(atom)

          if not atoms:
              st.session_state.todos_processed_raw.extend(new_raws)
              return 0

          with st.spinner("새 할 일 항목에 마감일·카테고리를 부여하는 중..."):
              enriched = enrich_with_gpt(atoms)

          existing_texts = {
              item["text"]
              for item in st.session_state.todos_v2
          }

          added = 0

          for e in enriched:
              if e["text"] in existing_texts:
                  continue

              st.session_state.todos_v2.append({
                  "id": f"todo_{int(datetime.now().timestamp() * 1000)}_{added}",
                  "text": e["text"],
                  "owner": e["owner"],
                  "due_date": e["due_date"],
                  "category": e["category"],
                  "done": False,
                  "source": "meeting"
              })

              existing_texts.add(e["text"])
              added += 1

          st.session_state.todos_processed_raw.extend(new_raws)

          return added


      def days_until(date_str):
          try:
              target = datetime.strptime(date_str, "%Y-%m-%d").date()
              today = datetime.now(ZoneInfo("Asia/Seoul")).date()
              return (target - today).days
          except Exception:
              return 9999


      def dday_label(date_str):
          d = days_until(date_str)

          if d < 0:
              return "D+" + str(abs(d)) + " (지연)"

          if d == 0:
              return "D-DAY"

          return "D-" + str(d)


      added = migrate_new_todos()

      if added > 0:
          st.toast(f"새 할 일 {added}건이 추가되었습니다.", icon="✨")

      st.title("✅ To-Do List")
      st.caption("회의록 분석 결과는 항목별로 자동 분리되며, 마감일 임박 순으로 정렬됩니다.")

      st.markdown(
          """
          <style>
          .attendee-strip {
              background: linear-gradient(135deg, #f3f0ff 0%, #e7f5ff 100%);
              border: 1px solid #dbe4ff;
              border-radius: 18px;
              padding: 14px 18px;
              margin-bottom: 18px;
              display: flex;
              flex-wrap: wrap;
              gap: 8px;
              align-items: center;
          }

          .attendee-strip-title {
              font-size: 13px;
              font-weight: 700;
              color: #495057;
              letter-spacing: 1px;
              margin-right: 6px;
          }

          .attendee-chip {
              display: inline-flex;
              align-items: center;
              gap: 6px;
              background: #ffffff;
              color: #343a40;
              padding: 6px 12px;
              border-radius: 999px;
              font-size: 13px;
              font-weight: 600;
              border: 1px solid #e9ecef;
              box-shadow: 0 1px 2px rgba(0,0,0,0.04);
          }

          .attendee-chip.me {
              background: linear-gradient(135deg, #ffd43b 0%, #fab005 100%);
              color: #5c3c00;
              border-color: #f08c00;
          }

          .progress-card {
              background: #fff;
              border: 1px solid #e9ecef;
              border-radius: 16px;
              padding: 14px 18px;
              margin-bottom: 18px;
              box-shadow: 0 2px 8px rgba(0,0,0,0.04);
          }

          .progress-stats {
              font-size: 13px;
              color: #495057;
              margin-bottom: 8px;
              font-weight: 600;
          }

          .progress-bar-wrap {
              background: #f1f3f5;
              border-radius: 999px;
              height: 12px;
              overflow: hidden;
          }

          .progress-bar-fill {
              height: 100%;
              background: linear-gradient(90deg, #51cf66 0%, #37b24d 100%);
              border-radius: 999px;
              transition: width .3s ease;
          }

          .todo-section-title {
              font-size: 17px;
              font-weight: 700;
              color: #212529;
              margin: 22px 0 10px 0;
              padding-left: 4px;
          }

          .owner-group-header {
              display: inline-flex;
              align-items: center;
              gap: 8px;
              padding: 6px 14px;
              border-radius: 999px;
              font-size: 13px;
              font-weight: 700;
              margin-bottom: 10px;
          }

          .owner-mine {
              background: #fff3bf;
              color: #5c3c00;
              border: 1px solid #fab005;
          }

          .owner-other {
              background: #e7f5ff;
              color: #1864ab;
              border: 1px solid #74c0fc;
          }

          .owner-shared {
              background: #f1f3f5;
              color: #495057;
              border: 1px solid #ced4da;
          }

          .todo-item {
              display: flex;
              align-items: flex-start;
              gap: 10px;
              background: #ffffff;
              border: 1px solid #e9ecef;
              border-left: 4px solid #adb5bd;
              border-radius: 12px;
              padding: 12px 14px;
              margin-bottom: 8px;
              transition: all .2s;
          }

          .todo-item.urgent {
              border-left-color: #fa5252;
              background: #fff5f5;
          }

          .todo-item.warning {
              border-left-color: #fab005;
              background: #fff9db;
          }

          .todo-item.normal {
              border-left-color: #51cf66;
          }

          .todo-item.done {
              background: #f8f9fa;
              opacity: 0.65;
          }

          .todo-text {
              font-size: 14px;
              color: #212529;
              font-weight: 500;
              line-height: 1.5;
          }

          .todo-text.done {
              text-decoration: line-through;
              color: #868e96;
          }

          .todo-meta {
              display: flex;
              flex-wrap: wrap;
              gap: 6px;
              margin-top: 6px;
          }

          .meta-tag {
              display: inline-block;
              font-size: 11px;
              font-weight: 600;
              padding: 2px 8px;
              border-radius: 999px;
              background: #f1f3f5;
              color: #495057;
          }

          .meta-tag.cat {
              background: #e7f5ff;
              color: #1971c2;
          }

          .meta-tag.due {
              background: #fff0f6;
              color: #c2255c;
          }

          .meta-tag.due.urgent {
              background: #fa5252;
              color: #fff;
          }

          .meta-tag.due.warning {
              background: #fab005;
              color: #fff;
          }

          .empty-card {
              background: #f8f9fa;
              border: 1px dashed #ced4da;
              border-radius: 14px;
              padding: 18px;
              color: #868e96;
              font-size: 14px;
              text-align: center;
          }
          </style>
          """,
          unsafe_allow_html=True
      )

      owners_in_todos = []

      for it in st.session_state.todos_v2:
          owner = it.get("owner")

          if owner and owner != "공용/미지정" and owner not in owners_in_todos:
              owners_in_todos.append(owner)

      chips_parts = ['<div class="attendee-strip">']
      chips_parts.append('<span class="attendee-strip-title">👥 회의 참석자</span>')
      chips_parts.append('<span class="attendee-chip me">⭐ ' + u["name"] + ' (나)</span>')

      for owner in owners_in_todos:
          if owner == u["name"]:
              continue

          chips_parts.append('<span class="attendee-chip">🧑 ' + owner + '</span>')

      if not owners_in_todos:
          chips_parts.append(
              '<span style="color:#868e96; font-size:13px; margin-left:4px;">아직 다른 참석자가 추출되지 않았습니다.</span>'
          )

      chips_parts.append('</div>')

      st.markdown(
          "".join(chips_parts),
          unsafe_allow_html=True
      )

      total_n = len(st.session_state.todos_v2)
      done_n = sum(
          1 for it in st.session_state.todos_v2
          if it.get("done")
      )

      progress_pct = int((done_n / total_n) * 100) if total_n else 0

      progress_html = (
          '<div class="progress-card">'
          '<div class="progress-stats">📊 진행률 &nbsp;·&nbsp; '
          + str(done_n)
          + ' / '
          + str(total_n)
          + ' 완료 &nbsp;·&nbsp; '
          + str(progress_pct)
          + '%</div>'
          '<div class="progress-bar-wrap"><div class="progress-bar-fill" style="width:'
          + str(progress_pct)
          + '%;"></div></div>'
          '</div>'
      )

      st.markdown(
          progress_html,
          unsafe_allow_html=True
      )

      with st.expander("✏️ 할 일 직접 추가하기", expanded=False):
          with st.form("manual_add_todo", clear_on_submit=True):
              ca, cb = st.columns([2, 1])

              with ca:
                  manual_text = st.text_input(
                      "할 일 내용",
                      placeholder="예) 디자인 시안 검토"
                  )

              with cb:
                  owner_options = (
                      ["나"]
                      + [o for o in owners_in_todos if o != u["name"]]
                      + ["공용/미지정", "직접 입력..."]
                  )

                  owner_choice = st.selectbox(
                      "담당자",
                      owner_options
                  )

              cc, cd, ce = st.columns([1, 1, 1])

              with cc:
                  owner_custom = st.text_input(
                      "담당자 직접 입력",
                      placeholder="예) 박차장",
                      disabled=(owner_choice != "직접 입력...")
                  )

              with cd:
                  default_due = (
                      datetime.now(ZoneInfo("Asia/Seoul")) + timedelta(days=3)
                  ).date()

                  manual_due = st.date_input(
                      "마감일",
                      value=default_due
                  )

              with ce:
                  manual_category = st.text_input(
                      "카테고리",
                      placeholder="예) 보고"
                  )

              submitted = st.form_submit_button(
                  "➕ 추가",
                  use_container_width=True
              )

              if submitted:
                  if not manual_text.strip():
                      st.warning("할 일 내용을 입력해주세요.")
                  else:
                      if owner_choice == "나":
                          owner_final = u["name"]
                      elif owner_choice == "직접 입력...":
                          owner_final = owner_custom.strip() or "공용/미지정"
                      else:
                          owner_final = owner_choice

                      st.session_state.todos_v2.append({
                          "id": f"todo_manual_{int(datetime.now().timestamp() * 1000)}",
                          "text": manual_text.strip(),
                          "owner": owner_final,
                          "due_date": manual_due.strftime("%Y-%m-%d"),
                          "category": (manual_category.strip() or "기타")[:8],
                          "done": False,
                          "source": "manual"
                      })

                      st.success("할 일이 추가되었습니다.")
                      st.rerun()

      col_clear, col_email = st.columns([1, 1.5])

      with col_clear:
          st.write("")

          if st.button("🧹 완료된 항목 지우기", use_container_width=True):
              st.session_state.todos_v2 = [
                  it for it in st.session_state.todos_v2
                  if not it.get("done")
              ]
              st.rerun()

      with col_email:
          email_target = st.text_input(
              "수신자 이메일 주소",
              placeholder="major0404@naver.com",
              key="target_email_address"
          )

          if st.button("✉️ 이메일로 할일 전송", use_container_width=True):
              if not email_target:
                  st.warning("이메일 주소를 입력해 주세요.")
              else:
                  now_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")

                  email_subject = f"[AIVLE] {u['name']}님의 실시간 To-Do List 공유 ({now_str})"

                  email_body = (
                      f"안녕하세요, {u['name']}님.\n"
                      "에이블입니다.\n\n"
                      "현재 활성화된 To-Do List를 전달드립니다.\n"
                      "-----------------------------------------\n"
                  )

                  sorted_for_mail = sorted(
                      st.session_state.todos_v2,
                      key=lambda x: (
                          x.get("done", False),
                          days_until(x.get("due_date", "9999-12-31"))
                      )
                  )

                  for idx, it in enumerate(sorted_for_mail, start=1):
                      status = "[완료]" if it.get("done") else "[대기]"

                      email_body += (
                          f"{idx}. {status} "
                          f"({it.get('owner', '-')}) "
                          f"[{it.get('category', '-')}] "
                          f"{it.get('text', '')} "
                          f"· 마감 {it.get('due_date', '-')}\n"
                      )

                  email_body += (
                      "-----------------------------------------\n\n"
                      "오늘도 생산적인 하루 되세요!\n\n"
                      "- AI 비서 에이블 올림"
                  )

                  with st.spinner("이메일을 발송하고 있습니다..."):
                      mail_res = send_email(
                          to=email_target,
                          subject=email_subject,
                          body=email_body
                      )

                      if mail_res.get("status") == "success":
                          st.success(f"🎉 {email_target} 주소로 To-Do List 이메일을 성공적으로 보냈습니다!")
                      else:
                          st.error(f"이메일 전송 실패: {mail_res.get('error')}")

      st.markdown("---")

      if not st.session_state.todos_v2:
          st.markdown(
              '<div class="empty-card">아직 등록된 할 일이 없습니다.<br/>챗봇에서 회의록을 분석하거나, 위의 할 일 직접 추가하기를 사용해 보세요.</div>',
              unsafe_allow_html=True
          )

      else:
          def sort_key(it):
              return (
                  it.get("done", False),
                  days_until(it.get("due_date", "9999-12-31"))
              )

          current_name = u["name"]

          mine_items = sorted(
              [
                  it for it in st.session_state.todos_v2
                  if it.get("owner") == current_name
              ],
              key=sort_key
          )

          others_map = {}

          for it in st.session_state.todos_v2:
              owner = it.get("owner")

              if owner == current_name or owner == "공용/미지정":
                  continue

              others_map.setdefault(owner, []).append(it)

          for owner in others_map:
              others_map[owner].sort(key=sort_key)

          shared_items = sorted(
              [
                  it for it in st.session_state.todos_v2
                  if it.get("owner") == "공용/미지정"
              ],
              key=sort_key
          )

          def render_todo_item(it):
              d = days_until(it.get("due_date", "9999-12-31"))

              if it.get("done"):
                  urgency_class = "done"
                  due_extra = ""
              elif d <= 1:
                  urgency_class = "urgent"
                  due_extra = "urgent"
              elif d <= 3:
                  urgency_class = "warning"
                  due_extra = "warning"
              else:
                  urgency_class = "normal"
                  due_extra = ""

              col_chk, col_body = st.columns([0.06, 0.94])

              with col_chk:
                  new_done = st.checkbox(
                      " ",
                      value=it.get("done", False),
                      key=f"chk_{it['id']}",
                      label_visibility="collapsed"
                  )

                  if new_done != it.get("done"):
                      for x in st.session_state.todos_v2:
                          if x["id"] == it["id"]:
                              x["done"] = new_done

                      st.rerun()

              with col_body:
                  text_cls = "todo-text done" if it.get("done") else "todo-text"

                  item_html = (
                      '<div class="todo-item ' + urgency_class + '">'
                      '<div style="flex:1;">'
                      '<div class="' + text_cls + '">' + it.get("text", "") + '</div>'
                      '<div class="todo-meta">'
                      '<span class="meta-tag cat">🏷️ ' + it.get("category", "기타") + '</span>'
                      '<span class="meta-tag due ' + due_extra + '">📅 ' + it.get("due_date", "-") + ' · ' + dday_label(it.get("due_date", "9999-12-31")) + '</span>'
                      '</div>'
                      '</div>'
                      '</div>'
                  )

                  st.markdown(
                      item_html,
                      unsafe_allow_html=True
                  )

          st.markdown(
              '<div class="todo-section-title">🌟 나의 할 일</div>',
              unsafe_allow_html=True
          )

          st.markdown(
              '<span class="owner-group-header owner-mine">👤 '
              + current_name
              + ' (나) · 총 '
              + str(len(mine_items))
              + '건</span>',
              unsafe_allow_html=True
          )

          if mine_items:
              for it in mine_items:
                  render_todo_item(it)
          else:
              st.markdown(
                  '<div class="empty-card">현재 나에게 할당된 할 일이 없습니다.</div>',
                  unsafe_allow_html=True
              )

          if others_map:
              st.markdown(
                  '<div class="todo-section-title">👥 팀원별 할 일</div>',
                  unsafe_allow_html=True
              )

              cols = st.columns(2)

              for i, owner in enumerate(sorted(others_map.keys())):
                  items = others_map[owner]

                  with cols[i % 2]:
                      st.markdown(
                          '<span class="owner-group-header owner-other">🧑‍💼 '
                          + owner
                          + ' · 총 '
                          + str(len(items))
                          + '건</span>',
                          unsafe_allow_html=True
                      )

                      for it in items:
                          render_todo_item(it)

          if shared_items:
              st.markdown(
                  '<div class="todo-section-title">📋 공용 / 담당자 미지정</div>',
                  unsafe_allow_html=True
              )

              st.markdown(
                  '<span class="owner-group-header owner-shared">🗂️ 공용 · 총 '
                  + str(len(shared_items))
                  + '건</span>',
                  unsafe_allow_html=True
              )

              for it in shared_items:
                  render_todo_item(it)
# -----------------------------
# 실행
# -----------------------------
if st.session_state.page == "login":
    render_login_page()
else:
    render_personal_page()
