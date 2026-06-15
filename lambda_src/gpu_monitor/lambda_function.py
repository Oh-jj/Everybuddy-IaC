"""
everybuddy — laurel GPU 서버 모니터링 에이전트
EventBridge 2분 주기 → Prometheus/Loki 조회 → 이상 감지 → Bedrock 분석 → Slack 알림
"""
import json
import os
import re
import hashlib
import boto3
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

# ── 환경 변수 ──────────────────────────────────────────────────────────────────
PROMETHEUS_ENDPOINT = os.environ["PROMETHEUS_ENDPOINT"]
LOKI_ENDPOINT       = os.environ["LOKI_ENDPOINT"]
SLACK_WEBHOOK_SSM   = os.environ["SLACK_WEBHOOK_SSM"]
BEDROCK_MODEL_ID    = os.environ.get(
    "BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20240620-v1:0"
)
BEDROCK_REGION   = os.environ.get("BEDROCK_REGION", "ap-southeast-1")
DATALAKE_BUCKET  = os.environ.get("DATALAKE_BUCKET", "")

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
s3      = boto3.client("s3",              region_name=BEDROCK_REGION)

_slack_url_cache: str | None = None

# ── 전역변수 쿨다운 (warm start 활용, IAM 불필요) ─────────────────────────────
COOLDOWN_MINUTES = 30
_last_alert_hash: str | None = None
_last_alert_time: float | None = None  # UTC timestamp


def _issue_hash(issues: list[str]) -> str:
    return hashlib.md5("|".join(sorted(issues)).encode()).hexdigest()[:8]


def check_cooldown(issues: list[str]) -> tuple[bool, int]:
    """
    Returns (should_alert, suppressed_count)
    - should_alert: True면 알림 전송
    - suppressed_count: 이번 쿨다운 사이클에서 억제된 횟수 (재알림 시 표시용)
    """
    global _last_alert_hash, _last_alert_time

    if not issues:
        _last_alert_hash = None
        _last_alert_time = None
        return False, 0

    h   = _issue_hash(issues)
    now = datetime.now(timezone.utc).timestamp()

    if _last_alert_hash == h and _last_alert_time is not None:
        elapsed = (now - _last_alert_time) / 60
        if elapsed < COOLDOWN_MINUTES:
            print(f"[INFO] 쿨다운 중 알림 억제 (동일 이슈, 경과 {elapsed:.0f}분/{COOLDOWN_MINUTES}분)")
            return False, 0

    # 새 이슈 또는 쿨다운 만료 → 알림 전송
    _last_alert_hash = h
    _last_alert_time = now
    return True, 0


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


# ── Loki 에러 로그 수집 ───────────────────────────────────────────────────────
def deduplicate_logs(lines: list[str], max_lines: int = 20) -> list[str]:
    """숫자/타임스탬프 제거 후 중복 패턴 묶기, 최대 N줄 반환"""
    seen: dict[str, dict] = {}
    for line in lines:
        pattern = re.sub(r'\d+', 'N', line)
        if pattern not in seen:
            seen[pattern] = {"line": line, "count": 1}
        else:
            seen[pattern]["count"] += 1

    result = []
    for info in list(seen.values())[:max_lines]:
        suffix = f" (×{info['count']})" if info["count"] > 1 else ""
        result.append(info["line"] + suffix)
    return result


def query_loki_errors(minutes: int = 2, limit: int = 50) -> list[str]:
    """Loki에서 최근 N분간 ERROR 이상 로그 수집"""
    try:
        now_ns   = int(datetime.now(timezone.utc).timestamp() * 1e9)
        start_ns = now_ns - int(minutes * 60 * 1e9)

        # Triton 컨테이너 로그만 조회 (service_name 레이블 기준)
        query = (
            '{service_name="triton"} |~ '
            '"(?i)(error|critical|fatal|exception|oom|killed|traceback|panic|cuda|segfault'
            '|failed to load|failed to create|server.*failed|restarting|failed)"'
        )
        params = urllib.parse.urlencode({
            "query":     query,
            "start":     start_ns,
            "end":       now_ns,
            "limit":     limit,
            "direction": "backward",
        })
        url = f"{LOKI_ENDPOINT}/loki/api/v1/query_range?{params}"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())

        if data.get("status") != "success":
            return []

        lines = []
        for stream in data["data"]["result"]:
            labels    = stream.get("stream", {})
            container = (
                labels.get("container_name")
                or labels.get("container")
                or labels.get("job", "unknown")
            )
            for _, msg in stream.get("values", []):
                msg = msg.strip()
                if msg:
                    lines.append(f"[{container}] {msg}")

        return deduplicate_logs(lines)

    except Exception as e:
        print(f"[WARN] Loki 쿼리 실패: {e}")
        return []


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

    # VRAM 급락 감지 — 10분 내 10GB 이상 감소 시 모델 크래시로 판단
    m["vram_drops"] = {}
    for r in prom_range("DCGM_FI_DEV_FB_USED{job='gpu-dcgm'}", minutes=10, step="60s"):
        gid  = gpu_id_from(r["metric"])
        vals = r.get("values", [])
        if len(vals) >= 2:
            first_v = float(vals[0][1])
            last_v  = float(vals[-1][1])
            drop    = first_v - last_v   # 양수 = 감소량
            if drop > 10_000:            # 10 GB 이상 감소
                m["vram_drops"][gid] = {
                    "from_mib": first_v,
                    "to_mib":   last_v,
                    "drop_mib": drop,
                }

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

    # VRAM 급락 — 모델 크래시 또는 컨테이너 재시작 감지
    for gid, vd in m.get("vram_drops", {}).items():
        issues.append(
            f"🔴 GPU {gid} VRAM 급락: {vd['from_mib']:.0f}→{vd['to_mib']:.0f} MiB"
            f" (−{vd['drop_mib']:.0f} MiB) — Triton 모델 크래시 또는 컨테이너 재시작"
        )
        bump("CRITICAL")

    # Triton 메트릭(포트 8002)은 네트워크 팀 포트 미개방으로 수집 불가 — 트리거에서 제외
    # 포트 개방 후 collect()에서 triton 데이터가 수집되면 프롬프트 컨텍스트로만 활용됨

    return issues, severity


# ── Bedrock 프롬프트 (메트릭 + 로그 통합) ────────────────────────────────────
def build_metric_prompt(m: dict, issues: list[str], log_lines: list[str]) -> str:
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
    log_str   = "\n".join(log_lines) if log_lines else "없음"

    return f"""당신은 GPU 서버 운영 전문가입니다. 아래 데이터를 분석하여 한국어로 간결하게 답하세요.

=== 서버 환경 (참고) ===
- 서버명: laurel (외부 온프레미스 GPU 서버)
- GPU: NVIDIA Tesla V100-DGXS-32GB × 4 (GPU 0~3)
- Triton Inference Server 컨테이너 (Docker, 2분마다 재시작 감지 체계 운영 중)
  - gemma_s2tt : GPU 0 (Speech-to-Text 번역)
  - gemma_v2tt : GPU 1 (Video-to-Text 번역)
  - gemma_t2tt : GPU 2, 3 (Text-to-Text 번역, 인스턴스 2개)
  - Supertonic_tts : CPU (TTS 합성)
- 정상 VRAM: 모델 로드 시 GPU당 약 15~16 GB / 미로드(아이들) 시 약 400 MB
- NVIDIA Driver 535 설치, 컨테이너 요구사항 560 이상 — "compatibility mode UNAVAILABLE" 경고는 정상 출력됨 (동작에는 영향 없음)
- 알려진 이슈: strict_readiness=1 설정으로 모델 일부 로드 실패 시 Triton 전체 재시작

=== laurel GPU 서버 상태 ({now}) ===

[GPU 현황]
{chr(10).join(lines) or "  데이터 없음"}

[시스템 메모리]
  {mem_str}

[Triton 추론 서버]
  {triton_str}

[감지된 이상 징후]
{issue_str}

[동시점 에러 로그]
{log_str}

=== 분석 요청 ===
1. 현재 상황을 한 줄로 요약하세요. (심각도 포함)
2. 위 환경 정보를 참고하여 이상 원인을 2~3줄로 정확히 추론하세요.
3. 즉시 취할 조치를 번호 목록으로 최대 3개 제시하세요. (구체적인 명령어 포함 권장)
4. 조치하지 않을 경우 30분 내 예상 상황을 한 줄로 예측하세요.

불필요한 설명 없이 핵심만 작성하세요."""


# ── Bedrock 프롬프트 (에러 로그 단독) ────────────────────────────────────────
def build_log_prompt(log_lines: list[str]) -> str:
    now     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log_str = "\n".join(log_lines)

    return f"""당신은 GPU 서버 운영 전문가입니다. 아래 에러 로그를 분석하여 한국어로 간결하게 답하세요.

=== 서버 환경 (참고) ===
- 서버명: laurel (외부 온프레미스 GPU 서버)
- GPU: NVIDIA Tesla V100-DGXS-32GB × 4
- Triton Inference Server (Docker 컨테이너) 운영 중
  - gemma_s2tt(GPU0), gemma_v2tt(GPU1), gemma_t2tt(GPU2,3), Supertonic_tts(CPU)
- NVIDIA Driver 535, 컨테이너 요구 560 이상 — 매 시작 시 "compatibility mode UNAVAILABLE" 경고는 정상
- strict_readiness=1: 모델 하나라도 실패 시 Triton 전체가 재시작됨

=== laurel GPU 서버 에러 로그 ({now}) ===

{log_str}

=== 분석 요청 ===
1. 어떤 컴포넌트에서 발생한 에러인지 한 줄로 설명하세요.
2. 위 환경 정보를 참고하여 에러 원인을 2~3줄로 정확히 추론하세요.
3. 즉시 취할 조치를 번호 목록으로 최대 3개 제시하세요. (구체적인 명령어 포함 권장)

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


# ── Slack 알림 — 메트릭 이상 ─────────────────────────────────────────────────
def send_slack_metric(issues: list[str], severity: str, analysis: str | None, m: dict):
    color   = {"CRITICAL": "#FF0000", "WARNING": "#FFA500"}.get(severity, "#36a64f")
    emoji   = {"CRITICAL": "🚨",      "WARNING": "⚠️"}.get(severity, "✅")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    gpu_ids   = sorted(set(list(m["temps"]) + list(m["utils"])))
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
                        "text": f"*🤖 Bedrock 분석*\n{analysis or '분석 불가'}",
                    },
                },
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "everybuddy · laurel GPU 모니터링 에이전트"}],
                },
            ],
        }]
    }
    _post_slack(payload)


# ── Slack 알림 — 에러 로그 단독 ──────────────────────────────────────────────
def send_slack_log(log_lines: list[str], analysis: str | None):
    now_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log_text = "\n".join(log_lines[:10])

    payload = {
        "attachments": [{
            "color": "#FF0000",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "🔴 [GPU 모니터] laurel 에러 로그 감지"},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*시각*\n{now_str}"},
                        {"type": "mrkdwn", "text": f"*감지 건수*\n{len(log_lines)}건"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*📋 에러 로그*\n```" + log_text + "```",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*🤖 Bedrock 분석*\n{analysis or '분석 불가'}",
                    },
                },
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "everybuddy · laurel GPU 모니터링 에이전트"}],
                },
            ],
        }]
    }
    _post_slack(payload)


def _post_slack(payload: dict):
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


# ── Data Lake 저장 ────────────────────────────────────────────────────────────
def save_to_datalake(
    severity: str,
    issues: list[str],
    log_lines: list[str],
    metrics: dict,
    analysis: str | None,
):
    if not DATALAKE_BUCKET:
        return

    now = datetime.now(timezone.utc)
    record = {
        "timestamp":       now.isoformat(),
        "severity":        severity,
        "issues":          issues,
        "loki_errors":     log_lines,
        "metrics_snapshot": {
            "temps":  {gid: td.get("current") for gid, td in metrics.get("temps", {}).items()},
            "utils":  metrics.get("utils", {}),
            "vrams":  {gid: v.get("pct") for gid, v in metrics.get("vrams", {}).items()},
            "powers": metrics.get("powers", {}),
        },
        "bedrock_analysis": analysis,
    }

    key = (
        f"laurel-errors/"
        f"year={now.strftime('%Y')}/"
        f"month={now.strftime('%m')}/"
        f"day={now.strftime('%d')}/"
        f"{int(now.timestamp())}_{severity}.json"
    )

    try:
        s3.put_object(
            Bucket=DATALAKE_BUCKET,
            Key=key,
            Body=json.dumps(record, ensure_ascii=False),
            ContentType="application/json",
        )
        print(f"[INFO] Data Lake 저장 완료: s3://{DATALAKE_BUCKET}/{key}")
    except Exception as e:
        print(f"[ERROR] Data Lake 저장 실패: {e}")


# ── Lambda 핸들러 ─────────────────────────────────────────────────────────────
def lambda_handler(event, context):
    print("[INFO] GPU 모니터링 에이전트 시작")

    # 1. 메트릭 수집 및 이상 감지
    m           = collect()
    issues, sev = detect(m)
    print(f"[INFO] severity={sev}, issues={len(issues)}")

    # 2. Loki 에러 로그 수집 (항상)
    log_lines = query_loki_errors()
    print(f"[INFO] error_logs={len(log_lines)}")

    bedrock_called = False

    # 3. 메트릭 이상 시 → 쿨다운 확인 후 Bedrock 분석 + Slack + Data Lake 저장
    if sev in ("WARNING", "CRITICAL"):
        should_alert, _ = check_cooldown(issues)
        if should_alert:
            analysis = call_bedrock(build_metric_prompt(m, issues, log_lines))
            send_slack_metric(issues, sev, analysis, m)
            save_to_datalake(sev, issues, log_lines, m, analysis)
            bedrock_called = True

    # 4. 메트릭 정상이지만 에러 로그 있을 때 → 로그 단독 분석 + Data Lake 저장 (쿨다운 미적용)
    elif log_lines:
        check_cooldown([])  # 메트릭 정상이면 상태 초기화
        log_analysis = call_bedrock(build_log_prompt(log_lines))
        send_slack_log(log_lines, log_analysis)
        save_to_datalake("LOG_ONLY", [], log_lines, m, log_analysis)
        bedrock_called = True

    # 5. 완전 정상 → 상태 초기화
    if sev == "NORMAL" and not log_lines:
        check_cooldown([])
        print("[INFO] 정상 범위 — 알림 생략")

    return {
        "severity":     sev,
        "issue_count":  len(issues),
        "error_logs":   len(log_lines),
        "bedrock_called": bedrock_called,
    }
