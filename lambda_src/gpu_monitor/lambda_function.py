"""
everybuddy — laurel GPU 서버 모니터링 에이전트
EventBridge 2분 주기 → Prometheus 조회 → 이상 감지 → Bedrock 분석 → Slack 알림
"""
import json
import os
import boto3
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

# ── 환경 변수 ──────────────────────────────────────────────────────────────────
PROMETHEUS_ENDPOINT = os.environ["PROMETHEUS_ENDPOINT"]
SLACK_WEBHOOK_SSM   = os.environ["SLACK_WEBHOOK_SSM"]
BEDROCK_MODEL_ID    = os.environ.get(
    "BEDROCK_MODEL_ID", "anthropic.claude-3-5-haiku-20241022-v1:0"
)
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "ap-southeast-1")

# ── 임계값 ────────────────────────────────────────────────────────────────────
THRESHOLDS = {
    "gpu_temp_warn":     75,    # °C
    "gpu_temp_critical": 82,    # °C  (V100 TJ Max ~85°C)
    "gpu_util_warn":     85,    # %
    "gpu_util_critical": 95,    # %
    "vram_warn":         80,    # %
    "vram_critical":     90,    # %
    "sys_mem_warn":      85,    # %
    "temp_trend_warn":   1.5,   # °C/분 — 급상승 판정
}

# ── AWS 클라이언트 ────────────────────────────────────────────────────────────
ssm     = boto3.client("ssm",             region_name=BEDROCK_REGION)
bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)

_slack_url_cache: str | None = None


def get_slack_url() -> str:
    global _slack_url_cache
    if not _slack_url_cache:
        resp = ssm.get_parameter(Name=SLACK_WEBHOOK_SSM, WithDecryption=True)
        _slack_url_cache = resp["Parameter"]["Value"]
    return _slack_url_cache


# ── Prometheus 헬퍼 ───────────────────────────────────────────────────────────
def prom_range(query: str, minutes: int = 10, step: str = "60s") -> list:
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes)
        params = urllib.parse.urlencode({
            "query": query,
            "start": start.timestamp(),
            "end":   end.timestamp(),
            "step":  step,
        })
        url = f"{PROMETHEUS_ENDPOINT}/api/v1/query_range?{params}"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        return data["data"]["result"] if data.get("status") == "success" else []
    except Exception as e:
        print(f"[WARN] prom_range '{query}': {e}")
        return []


def prom_instant(query: str) -> list:
    try:
        params = urllib.parse.urlencode({"query": query})
        url = f"{PROMETHEUS_ENDPOINT}/api/v1/query?{params}"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        return data["data"]["result"] if data.get("status") == "success" else []
    except Exception as e:
        print(f"[WARN] prom_instant '{query}': {e}")
        return []


def gpu_id_from(metric: dict) -> str:
    return metric.get("gpu") or metric.get("GPU_I_ID") or metric.get("uuid", "?")


def calc_trend(result_item: dict) -> float | None:
    try:
        vals = result_item["values"]
        if len(vals) < 2:
            return None
        dt = (vals[-1][0] - vals[0][0]) / 60
        return (float(vals[-1][1]) - float(vals[0][1])) / dt if dt else None
    except Exception:
        return None


# ── 메트릭 수집 ───────────────────────────────────────────────────────────────
def collect() -> dict:
    m: dict = {}

    # GPU 온도 (range: 트렌드 포함)
    m["temps"] = {}
    for r in prom_range("DCGM_FI_DEV_GPU_TEMP{job='gpu-dcgm'}"):
        gid = gpu_id_from(r["metric"])
        m["temps"][gid] = {
            "current": float(r["values"][-1][1]) if r["values"] else None,
            "trend":   calc_trend(r),
        }

    # GPU 사용률 (instant)
    m["utils"] = {}
    for r in prom_instant("DCGM_FI_DEV_GPU_UTIL{job='gpu-dcgm'}"):
        gid = gpu_id_from(r["metric"])
        m["utils"][gid] = float(r["value"][1])

    # VRAM (instant, MiB 단위)
    m["vrams"] = {}
    for r in prom_instant("DCGM_FI_DEV_FB_USED{job='gpu-dcgm'}"):
        gid = gpu_id_from(r["metric"])
        m["vrams"].setdefault(gid, {})["used"] = float(r["value"][1])
    for r in prom_instant("DCGM_FI_DEV_FB_FREE{job='gpu-dcgm'}"):
        gid = gpu_id_from(r["metric"])
        m["vrams"].setdefault(gid, {})["free"] = float(r["value"][1])
    for gid, v in m["vrams"].items():
        total      = v.get("used", 0) + v.get("free", 0)
        v["total"] = total
        v["pct"]   = v["used"] / total * 100 if total else 0

    # 전력 (instant)
    m["powers"] = {}
    for r in prom_instant("DCGM_FI_DEV_POWER_USAGE{job='gpu-dcgm'}"):
        gid = gpu_id_from(r["metric"])
        m["powers"][gid] = float(r["value"][1])

    # 시스템 메모리
    avail_r = prom_instant("node_memory_MemAvailable_bytes{job='gpu-node-exporter'}")
    total_r = prom_instant("node_memory_MemTotal_bytes{job='gpu-node-exporter'}")
    if avail_r and total_r:
        avail = float(avail_r[0]["value"][1])
        total = float(total_r[0]["value"][1])
        m["sys_mem"] = {
            "avail_gb": avail / 1024**3,
            "total_gb": total / 1024**3,
            "pct":      (total - avail) / total * 100 if total else 0,
        }
    else:
        m["sys_mem"] = None

    # Triton (포트 미개방 시 graceful 처리)
    req_r   = prom_instant("nv_inference_request_success{job='triton'}")
    queue_r = prom_instant("nv_inference_queue_duration_us{job='triton'}")
    if req_r or queue_r:
        m["triton"] = {
            "available": True,
            "requests":  float(req_r[0]["value"][1])   if req_r   else None,
            "queue_us":  float(queue_r[0]["value"][1]) if queue_r else None,
        }
    else:
        m["triton"] = {"available": False}

    return m


# ── 이상 감지 ─────────────────────────────────────────────────────────────────
def detect(m: dict) -> tuple[list[str], str]:
    issues:   list[str] = []
    severity: str       = "NORMAL"
    T = THRESHOLDS

    def bump(s: str):
        nonlocal severity
        if s == "CRITICAL" or (s == "WARNING" and severity == "NORMAL"):
            severity = s

    # 온도
    for gid, td in m["temps"].items():
        cur = td["current"]
        tr  = td["trend"]
        if cur is None:
            continue
        if cur >= T["gpu_temp_critical"]:
            issues.append(f"🔴 GPU {gid} 온도 위험: {cur:.1f}°C (임계 {T['gpu_temp_critical']}°C)")
            bump("CRITICAL")
        elif cur >= T["gpu_temp_warn"]:
            issues.append(f"🟡 GPU {gid} 온도 경고: {cur:.1f}°C (임계 {T['gpu_temp_warn']}°C)")
            bump("WARNING")
        if tr is not None and tr >= T["temp_trend_warn"]:
            issues.append(f"📈 GPU {gid} 온도 급상승: +{tr:.1f}°C/분 (현재 {cur:.1f}°C)")
            bump("WARNING")

    # 사용률
    for gid, util in m["utils"].items():
        if util >= T["gpu_util_critical"]:
            issues.append(f"🔴 GPU {gid} 사용률 위험: {util:.1f}%")
            bump("CRITICAL")
        elif util >= T["gpu_util_warn"]:
            issues.append(f"🟡 GPU {gid} 사용률 경고: {util:.1f}%")
            bump("WARNING")

    # VRAM
    for gid, v in m["vrams"].items():
        pct = v.get("pct")
        if pct is None:
            continue
        if pct >= T["vram_critical"]:
            vram_used = v.get("used", 0)
            vram_tot  = v.get("total", 0)
            issues.append(f"🔴 GPU {gid} VRAM 위험: {pct:.1f}% ({vram_used:.0f}/{vram_tot:.0f} MiB)")
            bump("CRITICAL")
        elif pct >= T["vram_warn"]:
            issues.append(f"🟡 GPU {gid} VRAM 경고: {pct:.1f}%")
            bump("WARNING")

    # 시스템 메모리
    sm = m.get("sys_mem")
    if sm and sm["pct"] >= T["sys_mem_warn"]:
        issues.append(f"🟡 시스템 메모리 경고: {sm['pct']:.1f}% (가용 {sm['avail_gb']:.1f}GB)")
        bump("WARNING")

    return issues, severity


# ── Bedrock 프롬프트 ──────────────────────────────────────────────────────────
def build_prompt(m: dict, issues: list[str]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    gpu_ids = sorted(set(list(m["temps"]) + list(m["utils"])))
    lines   = []
    for gid in gpu_ids:
        td    = m["temps"].get(gid, {})
        cur   = td.get("current")
        tr    = td.get("trend")
        util  = m["utils"].get(gid)
        vram  = m["vrams"].get(gid, {})
        pwr   = m["powers"].get(gid)

        temp_str  = (
            f"{cur:.1f}°C" + (f"(+{tr:.1f}/분)" if tr and tr > 0 else "")
            if cur is not None else "N/A"
        )
        util_str  = f"{util:.1f}%" if util is not None else "N/A"
        vram_pct  = vram.get("pct", 0)
        vram_used = vram.get("used", 0)
        vram_tot  = vram.get("total", 0)
        vram_str  = f"{vram_pct:.1f}%({vram_used:.0f}/{vram_tot:.0f}MiB)" if vram else "N/A"
        pwr_str   = f"{pwr:.0f}W" if pwr else "N/A"

        lines.append(
            f"  GPU {gid}: 온도={temp_str} | 사용률={util_str} | VRAM={vram_str} | 전력={pwr_str}"
        )

    sm = m.get("sys_mem")
    mem_str = (
        f"{sm['pct']:.1f}% (가용 {sm['avail_gb']:.1f}GB / 전체 {sm['total_gb']:.1f}GB)"
        if sm else "수집 불가"
    )

    triton = m.get("triton", {})
    triton_str = (
        f"요청수={triton.get('requests')} 큐대기={triton.get('queue_us')}μs"
        if triton.get("available") else "포트 미개방 (대기 중)"
    )

    issue_str = "\n".join(issues) if issues else "없음"

    return f"""당신은 GPU 서버 운영 전문가입니다. 아래 데이터를 분석하여 한국어로 간결하게 답하세요.

=== laurel GPU 서버 상태 ({now}) ===

[GPU 현황 — NVIDIA V100 x4]
{chr(10).join(lines) or "  데이터 없음"}

[시스템 메모리]
  {mem_str}

[Triton 추론 서버]
  {triton_str}

[감지된 이상 징후]
{issue_str}

=== 분석 요청 ===
1. 현재 상황 심각도를 한 줄로 요약하세요.
2. 이상 원인을 2~3줄로 추론하세요.
3. 즉시 취할 조치를 번호 목록으로 최대 3개 제시하세요.
4. 향후 30분 내 예상 상황을 한 줄로 예측하세요.

불필요한 설명 없이 핵심만 작성하세요."""


# ── Bedrock 호출 ──────────────────────────────────────────────────────────────
def call_bedrock(prompt: str) -> str | None:
    try:
        resp = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        return json.loads(resp["body"].read())["content"][0]["text"]
    except Exception as e:
        print(f"[ERROR] Bedrock 호출 실패: {e}")
        return None


# ── Slack 알림 ────────────────────────────────────────────────────────────────
def send_slack(issues: list[str], severity: str, analysis: str | None, m: dict):
    color   = {"CRITICAL": "#FF0000", "WARNING": "#FFA500"}.get(severity, "#36a64f")
    emoji   = {"CRITICAL": "🚨",       "WARNING": "⚠️"}.get(severity, "✅")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    gpu_ids = sorted(set(list(m["temps"]) + list(m["utils"])))
    gpu_lines = []
    for gid in gpu_ids:
        cur  = m["temps"].get(gid, {}).get("current")
        util = m["utils"].get(gid)
        pct  = m["vrams"].get(gid, {}).get("pct")
        parts = [f"GPU {gid}"]
        if cur  is not None: parts.append(f"{cur:.0f}°C")
        if util is not None: parts.append(f"사용률 {util:.0f}%")
        if pct  is not None: parts.append(f"VRAM {pct:.0f}%")
        gpu_lines.append(" | ".join(parts))

    payload = {
        "attachments": [{
            "color": color,
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"{emoji} [GPU 모니터] laurel 이상 감지 — {severity}"},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*시각*\n{now_str}"},
                        {"type": "mrkdwn", "text": f"*심각도*\n{severity}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*📊 GPU 현황*\n```" + "\n".join(gpu_lines or ["N/A"]) + "```",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*🔍 이상 징후*\n" + "\n".join(issues),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*🤖 Bedrock 분석*\n{analysis or '분석 불가 (Bedrock 오류)'}",
                    },
                },
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "everybuddy · laurel GPU 모니터링 에이전트 · Claude 3.5 Haiku"}],
                },
            ],
        }]
    }

    try:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            get_slack_url(), data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"[INFO] Slack 전송 완료: {r.status}")
    except Exception as e:
        print(f"[ERROR] Slack 전송 실패: {e}")


# ── Lambda 핸들러 ─────────────────────────────────────────────────────────────
def lambda_handler(event, context):
    print("[INFO] GPU 모니터링 에이전트 시작")

    m              = collect()
    issues, sev    = detect(m)
    print(f"[INFO] severity={sev}, issues={len(issues)}")

    if sev in ("WARNING", "CRITICAL"):
        analysis = call_bedrock(build_prompt(m, issues))
        send_slack(issues, sev, analysis, m)
    else:
        print("[INFO] 정상 범위 — Bedrock 호출 생략")

    return {
        "severity":       sev,
        "issue_count":    len(issues),
        "bedrock_called": sev != "NORMAL",
    }
