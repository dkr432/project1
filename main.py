import network
import socket
import time
import json
import gc
from machine import Pin, ADC
from neopixel import NeoPixel
from wifi_config import WIFI_NETWORKS, DISCORD_WEBHOOK_URL

# ===== 하드웨어 =====
gas_sensor = ADC(Pin(26))
TIMING = (280, 515, 515, 745)
NUM_LEDS = 10
led = NeoPixel(Pin(16), NUM_LEDS, timing=TIMING)

# ===== 임계값 =====
TH_IDLE   = 7000
TH_COOK   = 10000
TH_WARN   = 30000
TH_DANGER = 45000

# ===== 상태 변수 =====
timer_duration      = 600
timer_start         = 0
timer_running       = False
cooking             = False
last_gas            = 0
last_sensor         = 0
last_discord        = 0
last_led            = 0
idle_count          = 0
timer_done_notified = False
DISCORD_COOL        = 60000

# ===== 자동 캘리브레이션 =====
calib_state   = 'idle'   # idle / waiting / collecting / done
calib_start   = 0
calib_samples = []
calib_baseline = 0

# ===== LED =====
def clear_led():
    for i in range(NUM_LEDS):
        led[i] = (0, 0, 0)
    led.write()

def set_all(r, g, b):
    for i in range(NUM_LEDS):
        led[i] = (r, g, b)
    led.write()

def update_led():
    t = time.ticks_ms()

    # 캘리브레이션 중 → 보라색 물결
    if calib_state == 'waiting':
        b = (t // 300) % 2
        set_all(20, 0, 40) if b else set_all(5, 0, 10)
        return
    if calib_state == 'collecting':
        # 수집 진행도에 따라 파란색 LED 하나씩 증가
        elapsed = time.ticks_diff(t, calib_start) / 1000
        ratio   = min(1.0, elapsed / 10.0)
        on_cnt  = max(1, int(NUM_LEDS * ratio))
        for i in range(NUM_LEDS):
            led[i] = (0, 0, 50) if i < on_cnt else (0, 0, 0)
        led.write()
        return

    if last_gas >= TH_DANGER:
        b = (t // 80) % 2
        set_all(80, 0, 0) if b else clear_led()
        return
    if last_gas >= TH_WARN:
        b = (t // 500) % 2
        set_all(60, 30, 0) if b else clear_led()
        return
    if timer_running:
        elapsed   = time.ticks_diff(t, timer_start) / 1000
        remaining = max(0, timer_duration - elapsed)
        if remaining <= 0:
            b = (t // 300) % 2
            set_all(60, 0, 0) if b else clear_led()
            return
        if remaining <= 60:
            b = (t // 200) % 2
            set_all(50, 30, 0) if b else clear_led()
            return
        ratio  = elapsed / timer_duration
        on_cnt = max(0, int(NUM_LEDS * (1 - ratio)))
        for i in range(NUM_LEDS):
            led[i] = (0, 0, 50) if i < on_cnt else (0, 0, 0)
        led.write()
        return
    brightness = int((t % 3000) / 3000 * 25)
    if (t % 6000) >= 3000:
        brightness = 25 - brightness
    for i in range(NUM_LEDS):
        led[i] = (0, brightness, 0)
    led.write()

def get_state():
    elapsed   = time.ticks_diff(time.ticks_ms(), timer_start) / 1000 if timer_running else 0
    remaining = max(0, timer_duration - elapsed) if timer_running else None
    if last_gas >= TH_DANGER:
        return 'danger', remaining
    if last_gas >= TH_WARN:
        return 'gas_warn', remaining
    if timer_running and remaining is not None and remaining <= 0:
        return 'done', remaining
    if timer_running and remaining is not None and remaining <= 60:
        return 'timer_warn', remaining
    if timer_running:
        return 'running', remaining
    return 'idle', remaining

# ===== 캘리브레이션 로직 =====
def calib_tick():
    global calib_state, calib_start, calib_samples
    global TH_IDLE, TH_COOK, TH_WARN, TH_DANGER

    if calib_state == 'waiting':
        elapsed = time.ticks_diff(time.ticks_ms(), calib_start) / 1000
        # 5초 대기 후 수집 시작
        if elapsed >= 5:
            calib_state  = 'collecting'
            calib_start  = time.ticks_ms()
            calib_samples = []
            print("캘리브레이션: 수집 시작!")

    elif calib_state == 'collecting':
        # 0.3초마다 샘플 수집
        calib_samples.append(last_gas)
        elapsed = time.ticks_diff(time.ticks_ms(), calib_start) / 1000
        print(f"수집 중: {last_gas} ({len(calib_samples)}개, {elapsed:.1f}s)")

        # 10초 수집 완료
        if elapsed >= 10:
            if len(calib_samples) >= 3:
                # 임계값 계산
                baseline = calib_baseline
                peak     = max(calib_samples)
                avg      = sum(calib_samples) // len(calib_samples)
                rng      = max(peak - baseline, 3000)  # 최소 범위 3000

                TH_IDLE   = baseline + int(rng * 0.10)
                TH_COOK   = baseline + int(rng * 0.25)
                TH_WARN   = baseline + int(rng * 0.55)
                TH_DANGER = baseline + int(rng * 0.80)

                # 최솟값 보정
                TH_IDLE   = max(TH_IDLE,   baseline + 500)
                TH_COOK   = max(TH_COOK,   TH_IDLE   + 500)
                TH_WARN   = max(TH_WARN,   TH_COOK   + 1000)
                TH_DANGER = max(TH_DANGER, TH_WARN   + 1000)

                print(f"캘리브레이션 완료!")
                print(f"baseline={baseline} peak={peak} avg={avg}")
                print(f"IDLE={TH_IDLE} COOK={TH_COOK} WARN={TH_WARN} DANGER={TH_DANGER}")
                calib_state = 'done'
            else:
                calib_state = 'idle'
                print("캘리브레이션 실패: 샘플 부족")

# ===== 디스코드 =====
def discord_send(title, desc, color):
    try:
        import urequests
        d = json.dumps({"embeds": [{"title": title, "description": desc, "color": color}]})
        urequests.post(DISCORD_WEBHOOK_URL, data=d, headers={"Content-Type": "application/json"})
        print(f"디스코드: {title}")
    except Exception as e:
        print(f"디스코드 실패: {e}")

def discord_timer_start():
    m = timer_duration // 60
    s = timer_duration % 60
    t = f"{m}분" if s == 0 else f"{m}분 {s}초"
    discord_send("🍳 요리 시작!", f"가스레인지 감지!\n타이머: **{t}** 시작!", 0x3498DB)

def discord_timer_done():
    discord_send("⏰ 타이머 종료!", "요리 타이머 끝!\n**가스레인지를 확인하세요!**", 0xE67E22)

def discord_danger(value):
    global last_discord
    now = time.ticks_ms()
    if time.ticks_diff(now, last_discord) < DISCORD_COOL:
        return
    discord_send("🚨 위험! 가스 감지!", f"위험 수치: **{value}/65535**\n즉시 환기하세요!", 0xFF0000)
    last_discord = now

# ===== Wi-Fi =====
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    scan = [s[0].decode() for s in wlan.scan()]
    print(f"네트워크: {scan}")
    for ssid, pw in WIFI_NETWORKS.items():
        if ssid in scan:
            print(f"'{ssid}' 연결 중...")
            wlan.connect(ssid, pw)
            t = 15
            while not wlan.isconnected() and t > 0:
                time.sleep(1)
                t -= 1
            if wlan.isconnected():
                ip = wlan.ifconfig()[0]
                print(f"연결! IP: {ip}")
                return ip
            wlan.disconnect()
    return None

# ===== HTML =====
def get_html():
    return """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🍳 요리 비법서 🍳</title>
<link href="https://fonts.googleapis.com/css2?family=Nanum+Myeongjo:wght@400;700;800&family=Gowun+Batang:wght@400;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden}
body{
  font-family:'Gowun Batang','Nanum Myeongjo',serif;
  background:#3a2418;
  color:#3a2418;
  perspective:2000px;
}
.desk{
  position:fixed;top:0;left:0;width:100%;height:100%;
  background:radial-gradient(ellipse at center,#5a3a26 0%,#3a2418 70%,#2a1810 100%);
  z-index:0;
}
.desk::before{
  content:'';position:absolute;top:0;left:0;width:100%;height:100%;
  background-image:repeating-linear-gradient(45deg,rgba(0,0,0,.03) 0px,rgba(0,0,0,.03) 2px,transparent 2px,transparent 8px);
}
.floating-emoji{
  position:fixed;font-size:35px;opacity:.18;pointer-events:none;z-index:5;
  animation:float 20s linear infinite;filter:drop-shadow(0 2px 4px rgba(0,0,0,.3));
}
@keyframes float{
  0%{transform:translateY(100vh) rotate(0deg)}
  100%{transform:translateY(-100px) rotate(360deg)}
}
.book-wrap{
  position:relative;z-index:10;width:100%;height:100vh;
  display:flex;align-items:center;justify-content:center;padding:20px;
}
.book{
  position:relative;width:100%;max-width:700px;height:90vh;
  max-height:900px;transform-style:preserve-3d;
}
.page{
  position:absolute;top:0;left:0;width:100%;height:100%;
  background:linear-gradient(to right,#f0e0c0 0%,#f5e6cc 5%,#faecd4 100%);
  border-radius:8px 15px 15px 8px;padding:35px 30px;
  box-shadow:inset 5px 0 15px rgba(80,40,20,.3),0 10px 40px rgba(0,0,0,.5),0 20px 60px rgba(0,0,0,.3);
  overflow-y:auto;overflow-x:hidden;
  transform-origin:left center;
  transition:transform 1s cubic-bezier(.4,0,.2,1);
  backface-visibility:hidden;
}
.page::before{
  content:'';position:absolute;top:0;left:0;width:30px;height:100%;
  background:linear-gradient(to right,rgba(120,80,40,.3),rgba(120,80,40,.1) 40%,transparent);
  pointer-events:none;
}
.page::after{
  content:'';position:absolute;top:0;right:0;width:8px;height:100%;
  background:linear-gradient(to left,rgba(80,50,20,.4),transparent);
  pointer-events:none;
}
.page-inner{position:relative;z-index:2}
.page.flipping{transform:rotateY(-180deg)}
.page.flipped{transform:rotateY(-180deg);pointer-events:none}
.page-1{z-index:3}.page-2{z-index:2}.page-3{z-index:1}
.page-title{
  font-family:'Nanum Myeongjo',serif;font-size:28px;font-weight:800;
  text-align:center;color:#5a2818;margin-bottom:8px;padding-bottom:15px;
  border-bottom:3px double #8a4828;
}
.page-title::before,.page-title::after{content:'❦';margin:0 15px;color:#8a4828;font-size:20px}
.page-num{text-align:center;font-size:13px;color:#8a6848;margin-bottom:25px;letter-spacing:3px;font-style:italic}
.cover-icon{text-align:center;font-size:60px;margin:10px 0;filter:drop-shadow(2px 2px 4px rgba(0,0,0,.3))}
.status-row{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:20px 0}
.scard{
  background:linear-gradient(135deg,#faecd4,#e8d4b0);border:2px solid #8a4828;
  border-radius:10px;padding:12px 8px;text-align:center;position:relative;
  box-shadow:2px 2px 5px rgba(0,0,0,.2);
}
.scard::before{
  content:'';position:absolute;top:5px;left:5px;right:5px;bottom:5px;
  border:1px solid rgba(138,72,40,.3);border-radius:8px;pointer-events:none;
}
.scard-lb{font-size:10px;color:#8a6848;margin-bottom:5px;letter-spacing:1px;font-weight:700}
.scard-v{font-family:'Nanum Myeongjo',serif;font-size:22px;font-weight:800;color:#5a2818}
.badge{display:inline-block;padding:6px 16px;border-radius:20px;font-size:12px;font-weight:800;border:2px solid}
.b0{background:#d4e8b8;color:#2e5e0e;border-color:#2e5e0e}
.b1{background:#f5d4a0;color:#7e3e0e;border-color:#7e3e0e;animation:p .5s infinite}
.b2{background:#f5b8b8;color:#7e0e0e;border-color:#7e0e0e;animation:p .3s infinite}
.b3{background:#b8d4f5;color:#0e3e7e;border-color:#0e3e7e}
.b4{background:#f5c8a0;color:#7e3e0e;border-color:#7e3e0e;animation:p .4s infinite}
.b5{background:#f5e8a0;color:#7e6e0e;border-color:#7e6e0e;animation:p .5s infinite}
@keyframes p{50%{opacity:.5}}
.timer-wrap{text-align:center;margin:20px 0}
.timer-ring{width:240px;height:240px;margin:0 auto 15px;position:relative}
.timer-svg{width:100%;height:100%;transform:rotate(-90deg)}
.timer-bg{fill:none;stroke:#d4b890;stroke-width:6}
.timer-fill{
  fill:none;stroke:#8a4828;stroke-width:6;stroke-linecap:round;
  transition:stroke-dashoffset .5s ease;filter:drop-shadow(0 0 5px rgba(138,72,40,.5));
}
.timer-center{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center}
.timer-num{font-family:'Nanum Myeongjo',serif;font-size:48px;font-weight:800;color:#5a2818;line-height:1}
.timer-lb{font-size:11px;color:#8a6848;margin-top:5px;letter-spacing:2px}
.led-strip{
  display:flex;justify-content:center;gap:6px;padding:10px;
  background:#3a2418;border-radius:20px;margin:15px auto;
  width:fit-content;border:2px solid #8a4828;
}
.led-dot{width:18px;height:18px;border-radius:50%;background:#1a1a1a;border:1px solid #4a3a2a;transition:all .3s}
.led-on-g{background:radial-gradient(#0f0,#080);box-shadow:0 0 10px #0f0}
.led-on-b{background:radial-gradient(#48f,#06c);box-shadow:0 0 10px #48f}
.led-on-y{background:radial-gradient(#fa0,#c80);box-shadow:0 0 10px #fa0}
.led-on-r{background:radial-gradient(#f22,#a00);box-shadow:0 0 12px #f22}
.led-on-p{background:radial-gradient(#c080ff,#8040cc);box-shadow:0 0 10px #c080ff}
.timer-setting{
  background:rgba(212,184,144,.2);border:1px dashed #8a4828;
  border-radius:10px;padding:15px;margin:15px 0;
}
.row{display:flex;align-items:center;gap:10px;margin:10px 0;flex-wrap:wrap}
.row label{color:#5a2818;font-size:13px;font-weight:700;min-width:100px;display:flex;align-items:center;gap:5px}
.cur-val{color:#a04818;font-weight:800;background:#faecd4;padding:2px 8px;border-radius:4px;border:1px solid #8a4828}
input[type=range]{
  flex:1;min-width:120px;-webkit-appearance:none;appearance:none;
  height:6px;background:#d4b890;border-radius:3px;outline:none;border:1px solid #8a4828;
}
input[type=range]::-webkit-slider-thumb{
  -webkit-appearance:none;width:18px;height:18px;
  background:radial-gradient(#c08858,#8a4828);border-radius:50%;
  cursor:pointer;border:2px solid #5a2818;box-shadow:0 2px 4px rgba(0,0,0,.3);
}
input[type=range]::-moz-range-thumb{
  width:18px;height:18px;background:radial-gradient(#c08858,#8a4828);
  border-radius:50%;cursor:pointer;border:2px solid #5a2818;
}
input[type=number]{
  background:#faecd4;border:2px solid #8a4828;color:#5a2818;
  padding:5px 8px;border-radius:5px;width:70px;
  font-family:'Nanum Myeongjo',serif;font-weight:700;text-align:center;font-size:13px;
}
.btn{
  padding:10px 20px;border:2px solid;border-radius:8px;
  font-family:'Nanum Myeongjo',serif;font-size:13px;font-weight:800;
  cursor:pointer;margin:4px;transition:all .2s;box-shadow:2px 2px 0 rgba(0,0,0,.2);
}
.btn:hover{transform:translate(-1px,-1px);box-shadow:3px 3px 0 rgba(0,0,0,.3)}
.btn:active{transform:translate(1px,1px);box-shadow:1px 1px 0 rgba(0,0,0,.2)}
.btn-o{background:#f5c896;color:#5a2818;border-color:#5a2818}
.btn-r{background:#f5a8a8;color:#5a0808;border-color:#5a0808}
.btn-b{background:#a8c8f5;color:#082858;border-color:#082858}
.btn-p{background:#e8c8f5;color:#380858;border-color:#380858}
.msg{font-size:12px;margin-top:8px;min-height:16px;font-weight:700;text-align:center}

/* ===== 자동 캘리브레이션 박스 ===== */
.calib-box{
  background:rgba(180,140,200,.15);
  border:2px solid #8a5898;
  border-radius:12px;
  padding:18px;
  margin:20px 0;
  text-align:center;
}
.calib-box h3{
  color:#5a1878;border-left:4px solid #8a5898;
  padding-left:8px;text-align:left;margin-bottom:15px;
}
.calib-progress{
  display:none;
  margin-top:15px;
}
.calib-status-text{
  font-size:16px;font-weight:800;color:#5a1878;
  margin:10px 0;min-height:28px;
}
.calib-bar-wrap{
  height:20px;background:rgba(0,0,0,.1);
  border-radius:10px;overflow:hidden;margin:10px 0;
  border:1px solid #8a5898;
}
.calib-bar{
  height:100%;width:0%;
  background:linear-gradient(90deg,#8a5898,#c080ff);
  border-radius:10px;
  transition:width .3s;
}
.calib-countdown{
  font-family:'Nanum Myeongjo',serif;
  font-size:36px;font-weight:800;color:#5a1878;
  margin:10px 0;display:none;
}
.calib-result{
  display:none;
  background:rgba(100,200,100,.1);
  border:1px dashed #2e5e0e;
  border-radius:8px;
  padding:12px;
  margin-top:12px;
  text-align:left;
}
.calib-result-row{
  display:flex;justify-content:space-between;
  font-size:13px;font-weight:700;
  padding:4px 0;border-bottom:1px dashed rgba(138,72,40,.2);
  color:#3a2418;
}
.calib-result-row:last-child{border-bottom:none}
.calib-result-val{color:#a04818;font-weight:800}

.gas-meter{height:24px;background:#3a2418;border-radius:12px;overflow:hidden;margin:12px 0;border:2px solid #5a2818}
.gas-fill{height:100%;border-radius:12px;transition:width .5s,background .3s}
.meter-labels{display:flex;justify-content:space-between;font-size:10px;color:#5a2818;margin-top:5px;font-weight:700}
.chart-box{background:#faecd4;border:2px solid #8a4828;border-radius:10px;padding:15px;margin:15px 0}
.nav{
  position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
  display:flex;gap:10px;z-index:100;background:rgba(58,36,24,.9);
  padding:10px 15px;border-radius:30px;border:2px solid #8a4828;
  box-shadow:0 5px 20px rgba(0,0,0,.5);
}
.nav-btn{
  width:40px;height:40px;border-radius:50%;border:2px solid #faecd4;
  background:#5a2818;color:#faecd4;font-size:18px;font-weight:800;
  cursor:pointer;transition:all .2s;
}
.nav-btn:hover:not(:disabled){background:#8a4828;transform:scale(1.1)}
.nav-btn:disabled{opacity:.3;cursor:not-allowed}
.nav-dots{display:flex;align-items:center;gap:8px}
.nav-dot{width:10px;height:10px;border-radius:50%;background:#faecd4;opacity:.4;transition:all .3s}
.nav-dot.active{opacity:1;background:#ffa500;transform:scale(1.3)}
.deco{text-align:center;color:#8a4828;font-size:14px;margin:15px 0;letter-spacing:5px}
h3{font-family:'Nanum Myeongjo',serif;color:#5a2818;font-size:16px;margin:15px 0 10px;padding-left:8px;border-left:4px solid #8a4828}

@media(max-width:600px){
  .book-wrap{padding:10px 5px}
  .book{height:82vh;max-width:92%}
  .page{padding:25px 18px;border-radius:5px 10px 10px 5px}
  .page-title{font-size:22px}
  .timer-ring{width:200px;height:200px}
  .timer-num{font-size:38px}
  .scard-v{font-size:18px}
  .cover-icon{font-size:45px}
  .floating-emoji{font-size:28px;opacity:.22;z-index:5}
  .book{box-shadow:inset 5px 0 15px rgba(80,40,20,.3),0 8px 25px rgba(0,0,0,.6),0 15px 40px rgba(0,0,0,.4)}
}
@media(max-width:400px){
  .book-wrap{padding:5px 2px}
  .book{max-width:96%;height:80vh}
  .floating-emoji{font-size:24px;opacity:.25}
}
</style>
</head>
<body>
<div class="desk"></div>

<div class="floating-emoji" style="left:2%;animation-delay:0s">🍳</div>
<div class="floating-emoji" style="left:7%;animation-delay:3s">🍲</div>
<div class="floating-emoji" style="left:4%;animation-delay:6s">🥘</div>
<div class="floating-emoji" style="left:8%;animation-delay:9s">🍜</div>
<div class="floating-emoji" style="left:3%;animation-delay:12s">🥕</div>
<div class="floating-emoji" style="left:6%;animation-delay:15s">🧄</div>
<div class="floating-emoji" style="left:1%;animation-delay:18s">🧅</div>
<div class="floating-emoji" style="left:92%;animation-delay:1s">🥩</div>
<div class="floating-emoji" style="left:95%;animation-delay:4s">🍅</div>
<div class="floating-emoji" style="left:97%;animation-delay:7s">🌶️</div>
<div class="floating-emoji" style="left:93%;animation-delay:10s">🥒</div>
<div class="floating-emoji" style="left:96%;animation-delay:13s">🍆</div>
<div class="floating-emoji" style="left:91%;animation-delay:16s">🫕</div>
<div class="floating-emoji" style="left:98%;animation-delay:19s">🍕</div>
<div class="floating-emoji" style="left:94%;animation-delay:22s">🥗</div>
<div class="floating-emoji" style="left:50%;animation-delay:2s">🍞</div>
<div class="floating-emoji" style="left:50%;animation-delay:14s">🥖</div>

<div class="book-wrap">
  <div class="book">

    <!-- ===== 페이지 1 ===== -->
    <div class="page page-1" id="p1">
      <div class="page-inner">
        <div class="cover-icon">🍳📖</div>
        <h1 class="page-title">요리 비법서</h1>
        <div class="page-num">~ 第 一 章 · 요리 시작 ~</div>

        <div class="status-row">
          <div class="scard">
            <div class="scard-lb">🔥 가스 수치</div>
            <div class="scard-v" id="gV">--</div>
          </div>
          <div class="scard">
            <div class="scard-lb">📜 상태</div>
            <div style="margin-top:6px"><span class="badge b0" id="sB">대기중</span></div>
          </div>
          <div class="scard">
            <div class="scard-lb">⏳ 남은 시간</div>
            <div class="scard-v" id="tS">--:--</div>
          </div>
        </div>

        <div class="deco">⊰ ❀ ⊱</div>

        <div class="timer-wrap">
          <div class="timer-ring">
            <svg class="timer-svg" viewBox="0 0 100 100">
              <circle class="timer-bg" cx="50" cy="50" r="45"/>
              <circle class="timer-fill" id="timerFill" cx="50" cy="50" r="45"
                      stroke-dasharray="282.7" stroke-dashoffset="282.7"/>
            </svg>
            <div class="timer-center">
              <div class="timer-num" id="bigT">--:--</div>
              <div class="timer-lb">남은 시간</div>
            </div>
          </div>

          <div class="led-strip">
            <div class="led-dot"></div><div class="led-dot"></div>
            <div class="led-dot"></div><div class="led-dot"></div>
            <div class="led-dot"></div><div class="led-dot"></div>
            <div class="led-dot"></div><div class="led-dot"></div>
            <div class="led-dot"></div><div class="led-dot"></div>
          </div>
        </div>

        <div class="timer-setting">
          <h3>⏱ 타이머 설정</h3>
          <div class="row">
            <label>분 <span class="cur-val" id="minV">10</span></label>
            <input type="range" id="minR" min="0" max="60" value="10" oninput="minN.value=this.value;minV.textContent=this.value">
            <input type="number" id="minN" value="10" min="0" max="60" oninput="minR.value=this.value;minV.textContent=this.value">
          </div>
          <div class="row">
            <label>초 <span class="cur-val" id="secV">0</span></label>
            <input type="range" id="secR" min="0" max="59" value="0" oninput="secN.value=this.value;secV.textContent=this.value">
            <input type="number" id="secN" value="0" min="0" max="59" oninput="secR.value=this.value;secV.textContent=this.value">
          </div>
          <div style="text-align:center;margin-top:10px">
            <button class="btn btn-o" onclick="applyTimer()">✅ 적용</button>
            <button class="btn btn-r" onclick="stopTimer()">⏹ 중지</button>
          </div>
          <div class="msg" id="timerMsg"></div>
        </div>
      </div>
    </div>

    <!-- ===== 페이지 2 : 임계값 + 자동 설정 ===== -->
    <div class="page page-2" id="p2">
      <div class="page-inner">
        <div class="cover-icon">⚙️📜</div>
        <h1 class="page-title">감지 비법</h1>
        <div class="page-num">~ 第 二 章 · 임계값 조절 ~</div>

        <div class="deco">⊰ 🌿 ⊱</div>

        <!-- 자동 캘리브레이션 -->
        <div class="calib-box">
          <h3>🔮 임계값 자동 설정</h3>
          <p style="font-size:13px;color:#5a2818;margin-bottom:12px;line-height:1.6">
            버튼을 누르면 <b>가스불을 켜달라는 안내</b>가 나옵니다.<br>
            5초 대기 후 <b>10초 동안 센서값을 수집</b>하여<br>
            임계값 4개를 <b>자동으로 계산</b>해드립니다!
          </p>
          <button class="btn btn-p" onclick="startCalib()" id="calibBtn">🔮 자동 설정 시작</button>

          <div class="calib-progress" id="calibProgress">
            <div class="calib-status-text" id="calibStatusText"></div>
            <div class="calib-countdown" id="calibCountdown"></div>
            <div class="calib-bar-wrap">
              <div class="calib-bar" id="calibBar"></div>
            </div>
          </div>

          <div class="calib-result" id="calibResult">
            <div style="font-size:14px;font-weight:800;color:#2e5e0e;margin-bottom:10px">✅ 자동 설정 완료!</div>
            <div class="calib-result-row">
              <span>💨 가스없음</span><span class="calib-result-val" id="rIdle">--</span>
            </div>
            <div class="calib-result-row">
              <span>🍳 요리감지</span><span class="calib-result-val" id="rCook">--</span>
            </div>
            <div class="calib-result-row">
              <span>⚠️ 주의</span><span class="calib-result-val" id="rWarn">--</span>
            </div>
            <div class="calib-result-row">
              <span>🚨 위험</span><span class="calib-result-val" id="rDanger">--</span>
            </div>
          </div>
          <div class="msg" id="calibMsg"></div>
        </div>

        <!-- 수동 임계값 -->
        <h3>🔧 수동 조정</h3>
        <div class="row">
          <label>💨 가스없음 <span class="cur-val" id="idleV">7000</span></label>
          <input type="range" id="idleR" min="1000" max="20000" step="500" value="7000" oninput="idleV.textContent=this.value;idleN.value=this.value">
          <input type="number" id="idleN" value="7000" min="1000" max="20000" oninput="idleR.value=this.value;idleV.textContent=this.value">
        </div>
        <div class="row">
          <label>🍳 요리감지 <span class="cur-val" id="cookV">10000</span></label>
          <input type="range" id="cookR" min="1000" max="40000" step="500" value="10000" oninput="cookV.textContent=this.value;cookN.value=this.value">
          <input type="number" id="cookN" value="10000" min="1000" max="40000" oninput="cookR.value=this.value;cookV.textContent=this.value">
        </div>
        <div class="row">
          <label>⚠️ 주의 <span class="cur-val" id="warnV">30000</span></label>
          <input type="range" id="warnR" min="5000" max="60000" step="500" value="30000" oninput="warnV.textContent=this.value;warnN.value=this.value">
          <input type="number" id="warnN" value="30000" min="5000" max="60000" oninput="warnR.value=this.value;warnV.textContent=this.value">
        </div>
        <div class="row">
          <label>🚨 위험 <span class="cur-val" id="dangerV">45000</span></label>
          <input type="range" id="dangerR" min="10000" max="65000" step="500" value="45000" oninput="dangerV.textContent=this.value;dangerN.value=this.value">
          <input type="number" id="dangerN" value="45000" min="10000" max="65000" oninput="dangerR.value=this.value;dangerV.textContent=this.value">
        </div>
        <div style="text-align:center;margin-top:15px">
          <button class="btn btn-b" onclick="applyThresh()">✅ 수동 적용</button>
        </div>
        <div class="msg" id="threshMsg"></div>
      </div>
    </div>

    <!-- ===== 페이지 3 ===== -->
    <div class="page page-3" id="p3">
      <div class="page-inner">
        <div class="cover-icon">📊📈</div>
        <h1 class="page-title">감지 기록부</h1>
        <div class="page-num">~ 第 三 章 · 실시간 측정 ~</div>
        <div class="deco">⊰ 🔥 ⊱</div>
        <h3>💨 현재 가스 농도</h3>
        <div class="gas-meter"><div class="gas-fill" id="gBI" style="width:0%;background:linear-gradient(90deg,#5a8a3a,#8aba5a)"></div></div>
        <div class="meter-labels">
          <span>0</span>
          <span style="color:#2e5e0e">🌿 안전</span>
          <span style="color:#7e3e0e">⚠ 주의</span>
          <span style="color:#7e0e0e">🚨 위험</span>
          <span>65535</span>
        </div>
        <h3>📜 시간별 변화 기록</h3>
        <div class="chart-box"><canvas id="gC"></canvas></div>
        <div class="deco">⊰ 끝 ⊱</div>
      </div>
    </div>

  </div>
</div>

<div class="nav">
  <button class="nav-btn" id="prevBtn" onclick="prevPage()">◀</button>
  <div class="nav-dots">
    <div class="nav-dot active" id="dot0"></div>
    <div class="nav-dot" id="dot1"></div>
    <div class="nav-dot" id="dot2"></div>
  </div>
  <button class="nav-btn" id="nextBtn" onclick="nextPage()">▶</button>
</div>

<script>
let L=[],D=[],curTH={idle:7000,cook:10000,warn:30000,danger:45000},threshLoaded=false;
let currentPage=0;
let calibTimer=null;
let calibPhase='idle'; // idle / waiting / collecting / done
let calibStart=0;
const pages=[document.getElementById('p1'),document.getElementById('p2'),document.getElementById('p3')];

function updateNav(){
  document.getElementById('prevBtn').disabled=currentPage===0;
  document.getElementById('nextBtn').disabled=currentPage===2;
  for(let i=0;i<3;i++)
    document.getElementById('dot'+i).classList.toggle('active',i===currentPage);
}
function nextPage(){
  if(currentPage<2){
    pages[currentPage].classList.add('flipping');
    setTimeout(()=>{
      pages[currentPage].classList.remove('flipping');
      pages[currentPage].classList.add('flipped');
      currentPage++;updateNav();
    },500);
  }
}
function prevPage(){
  if(currentPage>0){
    currentPage--;
    pages[currentPage].classList.remove('flipped');
    pages[currentPage].classList.add('flipping');
    setTimeout(()=>{pages[currentPage].classList.remove('flipping');updateNav();},50);
  }
}
document.addEventListener('keydown',e=>{
  if(e.key==='ArrowRight')nextPage();
  else if(e.key==='ArrowLeft')prevPage();
});

// ===== 자동 캘리브레이션 =====
async function startCalib(){
  document.getElementById('calibBtn').disabled=true;
  document.getElementById('calibResult').style.display='none';
  document.getElementById('calibProgress').style.display='block';
  document.getElementById('calibMsg').textContent='';

  // 1단계: 서버에 캘리브레이션 시작 요청
  try{
    await fetch('/start_calib',{method:'POST'});
  }catch(e){console.error(e);}

  calibPhase='waiting';
  calibStart=Date.now();

  // 웹 카운트다운 표시
  runCalibUI();
}

function runCalibUI(){
  const statusEl=document.getElementById('calibStatusText');
  const countEl=document.getElementById('calibCountdown');
  const barEl=document.getElementById('calibBar');

  calibTimer=setInterval(async()=>{
    const elapsed=(Date.now()-calibStart)/1000;

    if(calibPhase==='waiting'){
      // 5초 대기 카운트다운
      const remain=Math.max(0,5-elapsed);
      statusEl.textContent='🔥 지금 가스불을 켜주세요!';
      countEl.style.display='block';
      countEl.textContent=Math.ceil(remain)+'초';
      barEl.style.width=(elapsed/5*100)+'%';
      barEl.style.background='linear-gradient(90deg,#c08858,#e0a858)';

      if(elapsed>=5){
        calibPhase='collecting';
        calibStart=Date.now();
        countEl.style.display='none';
      }
    }
    else if(calibPhase==='collecting'){
      // 10초 수집
      const remain=Math.max(0,10-elapsed);
      statusEl.textContent='📡 센서값 수집 중... 가스불을 켜둬주세요!';
      countEl.style.display='block';
      countEl.textContent=Math.ceil(remain)+'초';
      barEl.style.width=(elapsed/10*100)+'%';
      barEl.style.background='linear-gradient(90deg,#8a5898,#c080ff)';

      if(elapsed>=11){
        // 수집 완료 → 서버에서 결과 가져오기
        calibPhase='done';
        clearInterval(calibTimer);
        statusEl.textContent='⚙️ 계산 중...';
        countEl.style.display='none';
        barEl.style.width='100%';

        setTimeout(async()=>{
          try{
            let r=await fetch('/data');
            let j=await r.json();
            if(j.thresholds){
              let th=j.thresholds;
              curTH=th;
              threshLoaded=true;

              // 슬라이더 업데이트
              idleR.value=idleN.value=idleV.textContent=th.idle;
              cookR.value=cookN.value=cookV.textContent=th.cook;
              warnR.value=warnN.value=warnV.textContent=th.warn;
              dangerR.value=dangerN.value=dangerV.textContent=th.danger;

              // 결과 표시
              document.getElementById('rIdle').textContent=th.idle;
              document.getElementById('rCook').textContent=th.cook;
              document.getElementById('rWarn').textContent=th.warn;
              document.getElementById('rDanger').textContent=th.danger;
              document.getElementById('calibResult').style.display='block';

              statusEl.textContent='✅ 자동 설정 완료!';
              barEl.style.background='linear-gradient(90deg,#2e5e0e,#5aba3a)';

              let msg=document.getElementById('calibMsg');
              msg.style.color='#2e5e0e';
              msg.textContent='✅ 임계값이 자동으로 설정되었습니다!';
            }
          }catch(e){
            document.getElementById('calibMsg').textContent='❌ 결과 가져오기 실패';
          }
          document.getElementById('calibBtn').disabled=false;
        },1000);
      }
    }
  },200);
}

// ===== 차트 =====
const ch=new Chart(document.getElementById('gC'),{
  type:'line',
  data:{labels:L,datasets:[{
    data:D,borderColor:'#8a4828',backgroundColor:'rgba(138,72,40,.15)',
    borderWidth:2,fill:true,tension:.4,pointRadius:0
  }]},
  options:{
    responsive:true,animation:{duration:300},
    scales:{
      x:{ticks:{color:'#5a2818',maxTicksLimit:6},grid:{color:'rgba(138,72,40,.1)'}},
      y:{min:0,max:65535,ticks:{color:'#5a2818'},grid:{color:'rgba(138,72,40,.1)'}}
    },
    plugins:{legend:{display:false}}
  }
});

function fmt(s){
  let m=Math.floor(Math.max(0,s)/60),sc=Math.floor(Math.max(0,s)%60);
  return String(m).padStart(2,'0')+':'+String(sc).padStart(2,'0');
}

function updateLEDs(state,ratio){
  let ds=document.querySelectorAll('.led-dot'),bl=Date.now()%600<300;
  ds.forEach((d,i)=>{
    d.className='led-dot';
    if(state==='idle')d.classList.add('led-on-g');
    else if(state==='running'){if(i<Math.round((1-ratio)*10))d.classList.add('led-on-b');}
    else if(state==='calib_waiting'){if(bl)d.classList.add('led-on-p');}
    else if(state==='gas_warn'||state==='timer_warn'){if(bl)d.classList.add('led-on-y');}
    else if(state==='done'||state==='danger'){if(bl)d.classList.add('led-on-r');}
  });
}

async function applyTimer(){
  let m=parseInt(minN.value)||0,s=parseInt(secN.value)||0,total=m*60+s;
  if(total<=0){alert('시간을 설정해주세요!');return;}
  let r=await fetch('/set_timer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({duration:total})});
  let j=await r.json();
  let msg=document.getElementById('timerMsg');
  msg.style.color=j.ok?'#2e5e0e':'#7e0e0e';
  msg.textContent=j.ok?'✅ 적용! 가스 감지 시 자동 시작':'❌ 실패';
  setTimeout(()=>msg.textContent='',3000);
}
async function stopTimer(){
  await fetch('/stop_timer',{method:'POST'});
  let msg=document.getElementById('timerMsg');
  msg.style.color='#7e0e0e';msg.textContent='⏹ 타이머 중지됨';
  setTimeout(()=>msg.textContent='',3000);
}
async function applyThresh(){
  let data={idle:parseInt(idleN.value),cook:parseInt(cookN.value),warn:parseInt(warnN.value),danger:parseInt(dangerN.value)};
  let r=await fetch('/set_thresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  let j=await r.json();
  let msg=document.getElementById('threshMsg');
  msg.style.color=j.ok?'#2e5e0e':'#7e0e0e';
  msg.textContent=j.ok?'✅ 수동 적용됨!':'❌ 실패';
  if(j.ok){curTH=data;threshLoaded=true;}
  setTimeout(()=>msg.textContent='',3000);
}

async function fetchData(){
  try{
    let r=await fetch('/data');
    let j=await r.json();
    let v=j.value;

    if(j.thresholds&&!threshLoaded){
      let th=j.thresholds;curTH=th;
      idleR.value=idleN.value=idleV.textContent=th.idle;
      cookR.value=cookN.value=cookV.textContent=th.cook;
      warnR.value=warnN.value=warnV.textContent=th.warn;
      dangerR.value=dangerN.value=dangerV.textContent=th.danger;
      threshLoaded=true;
    }

    L.push('');D.push(v);
    if(L.length>100){L.shift();D.shift();}
    ch.update('none');

    document.getElementById('gV').textContent=v;
    let pct=Math.min(100,v/65535*100);
    let gbi=document.getElementById('gBI');
    gbi.style.width=pct+'%';
    if(v<curTH.cook)gbi.style.background='linear-gradient(90deg,#5a8a3a,#8aba5a)';
    else if(v<curTH.warn)gbi.style.background='linear-gradient(90deg,#c08838,#e0a858)';
    else gbi.style.background='linear-gradient(90deg,#a02818,#c04838)';

    let sb=document.getElementById('sB');
    let state=j.state,remaining=j.remaining,duration=j.duration;
    if(state==='danger'){sb.textContent='🚨 위험!';sb.className='badge b2';}
    else if(state==='gas_warn'){sb.textContent='⚠️ 주의';sb.className='badge b5';}
    else if(state==='done'){sb.textContent='⏰ 종료!';sb.className='badge b4';}
    else if(state==='timer_warn'){sb.textContent='⏰ 임박!';sb.className='badge b1';}
    else if(state==='running'){sb.textContent='🍳 요리중';sb.className='badge b3';}
    else{sb.textContent='✅ 대기중';sb.className='badge b0';}

    let fill=document.getElementById('timerFill');
    if(remaining===null||remaining===undefined){
      document.getElementById('bigT').textContent='--:--';
      document.getElementById('tS').textContent='--:--';
      fill.style.strokeDashoffset='282.7';
      updateLEDs('idle',0);
    }else{
      document.getElementById('bigT').textContent=fmt(remaining);
      document.getElementById('tS').textContent=fmt(remaining);
      let ratio=remaining/duration;
      fill.style.strokeDashoffset=(282.7*(1-ratio))+'';
      updateLEDs(state,ratio);
    }
  }catch(e){console.error('오류:',e);}
}

setInterval(fetchData,500);
fetchData();
setInterval(()=>{
  let bl=Date.now()%600<300;
  document.querySelectorAll('.led-dot').forEach(d=>{
    if(d.classList.contains('led-on-y')||d.classList.contains('led-on-r')||d.classList.contains('led-on-p'))
      d.style.opacity=bl?'1':'.2';
    else d.style.opacity='1';
  });
},150);
updateNav();
</script>
</body>
</html>"""

# ===== 서버 =====
def send_html(cl):
    html = get_html()
    cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n")
    for i in range(0, len(html), 512):
        cl.send(html[i:i+512].encode('utf-8'))
        time.sleep_ms(20)

def handle_client(cl):
    global timer_duration, timer_start, timer_running
    global cooking, idle_count, timer_done_notified
    global TH_IDLE, TH_COOK, TH_WARN, TH_DANGER
    global calib_state, calib_start, calib_samples, calib_baseline

    try:
        cl.settimeout(5)
        raw    = cl.recv(1024)
        req    = raw.decode('utf-8', 'ignore')
        if not req:
            return
        line   = req.split('\r\n')[0]
        parts  = line.split(' ')
        method = parts[0] if parts else 'GET'
        path   = parts[1] if len(parts) > 1 else '/'
        print(f"{method} {path}")

        if path == '/data':
            state, remaining = get_state()
            body = json.dumps({
                "value":      last_gas,
                "state":      state,
                "remaining":  int(remaining) if remaining is not None else None,
                "duration":   timer_duration,
                "calib":      calib_state,
                "thresholds": {"idle": TH_IDLE, "cook": TH_COOK, "warn": TH_WARN, "danger": TH_DANGER}
            })
            cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
            cl.send(body.encode())

        elif path == '/start_calib' and method == 'POST':
            # 캘리브레이션 시작
            calib_baseline = last_gas   # 현재 평상시값 저장
            calib_samples  = []
            calib_state    = 'waiting'
            calib_start    = time.ticks_ms()
            print(f"캘리브레이션 시작! baseline={calib_baseline}")
            cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
            cl.send(b'{"ok":true}')

        elif path == '/set_timer' and method == 'POST':
            try:
                data                = json.loads(req.split('\r\n\r\n')[-1])
                timer_duration      = int(data.get('duration', 600))
                timer_running       = False
                cooking             = False
                idle_count          = 0
                timer_done_notified = False
                print(f"타이머 설정: {timer_duration}초")
                cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
                cl.send(b'{"ok":true}')
            except:
                cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
                cl.send(b'{"ok":false}')

        elif path == '/stop_timer' and method == 'POST':
            timer_running       = False
            cooking             = False
            idle_count          = 0
            timer_done_notified = False
            cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
            cl.send(b'{"ok":true}')

        elif path == '/set_thresh' and method == 'POST':
            try:
                data      = json.loads(req.split('\r\n\r\n')[-1])
                TH_IDLE   = int(data.get('idle',   TH_IDLE))
                TH_COOK   = int(data.get('cook',   TH_COOK))
                TH_WARN   = int(data.get('warn',   TH_WARN))
                TH_DANGER = int(data.get('danger', TH_DANGER))
                print(f"임계값: {TH_IDLE} {TH_COOK} {TH_WARN} {TH_DANGER}")
                cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
                cl.send(b'{"ok":true}')
            except:
                cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
                cl.send(b'{"ok":false}')

        elif path == '/favicon.ico':
            cl.send(b"HTTP/1.1 204\r\n\r\n")

        else:
            send_html(cl)

    except Exception as e:
        print(f"요청 오류: {e}")
    finally:
        try:
            cl.close()
        except:
            pass

# ===== 메인 =====
def main():
    global last_gas, last_sensor, last_led
    global timer_running, timer_start
    global cooking, idle_count, timer_done_notified
    global calib_state, calib_start

    clear_led()
    gc.collect()

    ip = connect_wifi()
    if not ip:
        print("Wi-Fi 실패!")
        while True:
            time.sleep(1)

    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', 80))
    s.listen(2)
    s.settimeout(0.1)
    print(f"http://{ip} 접속하세요!")

    last_calib_tick = 0  # 캘리브레이션 샘플 수집 타이머

    while True:
        try:
            cl, addr = s.accept()
            handle_client(cl)
            gc.collect()
        except OSError:
            pass
        except Exception as e:
            print(f"서버 오류: {e}")

        now = time.ticks_ms()

        # LED 100ms마다
        if time.ticks_diff(now, last_led) >= 100:
            last_led = now
            update_led()

        # 센서 500ms마다
        if time.ticks_diff(now, last_sensor) >= 500:
            last_sensor = now
            last_gas    = gas_sensor.read_u16()
            print(f"가스: {last_gas}")

            # 위험 감지
            if last_gas >= TH_DANGER:
                discord_danger(last_gas)

            # 요리 시작 감지 (캘리브레이션 중 아닐 때만)
            if calib_state == 'idle' or calib_state == 'done':
                if not timer_running and last_gas >= TH_COOK:
                    idle_count = 0
                    if not cooking:
                        cooking             = True
                        timer_start         = time.ticks_ms()
                        timer_running       = True
                        timer_done_notified = False
                        print(f"요리 감지! {timer_duration}초 타이머 시작")
                        discord_timer_start()

                # 요리 종료 감지
                if cooking and last_gas < TH_IDLE:
                    idle_count += 1
                    if idle_count >= 6:
                        cooking             = False
                        timer_running       = False
                        idle_count          = 0
                        timer_done_notified = False
                        print("가스 꺼짐, 타이머 초기화")
                else:
                    if last_gas >= TH_IDLE:
                        idle_count = 0

                # 타이머 종료 체크
                if timer_running:
                    elapsed = time.ticks_diff(time.ticks_ms(), timer_start) / 1000
                    if elapsed >= timer_duration and not timer_done_notified:
                        timer_done_notified = True
                        print("타이머 종료!")
                        discord_timer_done()

        # 캘리브레이션 tick (300ms마다 샘플 수집)
        if calib_state in ('waiting', 'collecting'):
            if time.ticks_diff(now, last_calib_tick) >= 300:
                last_calib_tick = now
                calib_tick()

main()
