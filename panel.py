"""Painel web do bot (somente leitura). Roda numa thread dentro do bot.py.

Lê os arquivos que o bot já grava em STATE_DIR (state.json, equity.csv, trades.csv).
Protegido por senha (HTTP Basic, usuário "admin"). Não envia ordens nem muda nada.
"""
import base64
import csv
import hmac
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("panel")


def _read_csv(path: Path, limit: int = 5000) -> list:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))[-limit:]


def start(state_dir: Path, port: int, password: str, price_fn, config: dict):
    expected = "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()
    cache = {"t": 0.0, "price": None}

    def live_price():
        if time.time() - cache["t"] > 30:
            try:
                cache["price"] = price_fn()
            except Exception as e:  # sem preço ao vivo, o painel usa o do último candle
                log.warning("preço ao vivo falhou: %s", e)
            cache["t"] = time.time()
        return cache["price"]

    def status():
        f = state_dir / "state.json"
        s = json.loads(f.read_text()) if f.exists() else {}
        p = live_price() or s.get("price")
        out = {"config": config, "state": s, "live_price": p, "server_time": time.time()}
        if p and s.get("start_price"):
            out["live_equity"] = s.get("usdc", 0) + s.get("btc", 0) * p
            out["live_bh"] = s["start_equity"] * p / s["start_price"]
        return out

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="btcbot"')
                self.end_headers()
                return
            path = self.path.split("?")[0]
            try:
                if path == "/":
                    self._send(200, PAGE, "text/html; charset=utf-8")
                elif path == "/api/status":
                    self._send(200, json.dumps(status()), "application/json")
                elif path == "/api/equity":
                    self._send(200, json.dumps(_read_csv(state_dir / "equity.csv")), "application/json")
                elif path == "/api/trades":
                    self._send(200, json.dumps(_read_csv(state_dir / "trades.csv", 200)), "application/json")
                else:
                    self._send(404, "não encontrado", "text/plain; charset=utf-8")
            except Exception as e:
                log.warning("painel: %r", e)
                self._send(500, "erro interno", "text/plain; charset=utf-8")

    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("Painel web em http://0.0.0.0:%d (usuário: admin)", port)
    return srv


PAGE = r"""<!doctype html>
<html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>btcbot</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#14171c;--mut:#667085;--line:#e4e7ec;--up:#12855b;--down:#c4320a;--acc:#2f6fed;--bh:#98a2b3}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e8eaed;--mut:#98a2b3;--line:#262a33;--up:#3ccf91;--down:#ff7a59;--acc:#6b9bff;--bh:#667085}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 40px}
header{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:16px}
h1{font-size:20px;margin:0}.tag{font-size:12px;font-weight:600;padding:3px 8px;border-radius:99px;background:var(--line)}
.tag.sim{background:#2f6fed22;color:var(--acc)}.tag.real{background:#c4320a22;color:var(--down)}
.mut{color:var(--mut);font-size:13px}.sp{flex:1}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.k{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
.v{font-size:22px;font-weight:650;margin-top:4px;font-variant-numeric:tabular-nums}
.s{font-size:13px;color:var(--mut);margin-top:2px;font-variant-numeric:tabular-nums}
.up{color:var(--up)}.down{color:var(--down)}
.chart{height:320px}h2{font-size:15px;margin:0 0 10px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:14px}
th,td{text-align:left;padding:7px 6px;border-bottom:1px solid var(--line)}th{color:var(--mut);font-weight:500;font-size:12px}
.tw{overflow-x:auto}.empty{color:var(--mut);padding:30px 0;text-align:center}
</style></head><body><div class="wrap">
<header><h1>btcbot</h1><span id="mode" class="tag">…</span><span id="cfg" class="mut"></span><span class="sp"></span><span id="upd" class="mut"></span></header>
<div class="grid">
 <div class="card"><div class="k">Posição</div><div class="v" id="pos">…</div><div class="s" id="pos2"></div></div>
 <div class="card"><div class="k">Preço BTC</div><div class="v" id="price">…</div><div class="s" id="price2"></div></div>
 <div class="card"><div class="k">Patrimônio simulado</div><div class="v" id="eq">…</div><div class="s" id="eq2"></div></div>
 <div class="card"><div class="k">Comprar e segurar</div><div class="v" id="bh">…</div><div class="s" id="bh2"></div></div>
</div>
<div class="grid">
 <div class="card"><div class="k">EMAs (último candle)</div><div class="v" id="ema">…</div><div class="s" id="ema2"></div></div>
 <div class="card"><div class="k">Último candle</div><div class="v" id="cdl">…</div><div class="s" id="cdl2"></div></div>
</div>
<div class="card" style="margin-bottom:12px"><h2>Patrimônio: estratégia × comprar e segurar</h2><div class="chart"><canvas id="ch"></canvas><div id="chEmpty" class="empty" hidden>Ainda sem histórico. O primeiro ponto aparece no próximo fechamento de candle.</div></div></div>
<div class="card"><h2>Operações</h2><div class="tw" id="trades"></div></div>
</div>
<script>
const usd=v=>v==null?'–':'$'+Number(v).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
const pct=v=>(v>=0?'+':'')+(v*100).toFixed(2)+'%';
const cls=v=>v>=0?'up':'down';
const dt=s=>new Date(s).toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'});
const $=id=>document.getElementById(id);
let chart;
async function j(u){const r=await fetch(u,{cache:'no-store'});if(!r.ok)throw new Error(r.status);return r.json()}
async function load(){
 try{
  const [st,eq,tr]=await Promise.all([j('api/status'),j('api/equity'),j('api/trades')]);
  const c=st.config,s=st.state;
  $('mode').textContent=c.live?'REAL':'SIMULAÇÃO';$('mode').className='tag '+(c.live?'real':'sim');
  $('cfg').textContent=`${c.product} · ${c.tf} · EMA ${c.fast}/${c.slow}${c.trend?' · filtro '+c.trend:''}`;
  $('upd').textContent='atualizado '+new Date().toLocaleTimeString('pt-BR');
  $('pos').textContent=s.position?'Comprado em BTC':'Fora (USDC)';
  $('pos2').textContent=s.signal==null?'aguardando 1º candle':'sinal atual: '+(s.signal?'comprar':'ficar fora');
  $('price').textContent=usd(st.live_price);
  $('price2').textContent=s.start_price?'início: '+usd(s.start_price):'';
  const se=s.start_equity||0,le=st.live_equity??s.usdc,lb=st.live_bh;
  $('eq').textContent=usd(le);
  $('eq2').innerHTML=se?`<span class="${cls(le/se-1)}">${pct(le/se-1)}</span> desde ${s.start_time?dt(s.start_time):'o início'}`:'';
  $('bh').textContent=lb==null?'–':usd(lb);
  $('bh2').innerHTML=lb==null?'':`<span class="${cls(lb/se-1)}">${pct(lb/se-1)}</span> · diferença <span class="${cls(le/lb-1)}">${pct(le/lb-1)}</span>`;
  if(s.ema_fast){const d=s.ema_fast/s.ema_slow-1;
   $('ema').innerHTML=`<span class="${cls(d)}">${pct(d)}</span>`;
   $('ema2').textContent=`rápida ${usd(s.ema_fast)} · lenta ${usd(s.ema_slow)} · ${d>=0?'acima: sinal de compra':'abaixo: sinal de ficar fora'}`;}
  else{$('ema').textContent='–';$('ema2').textContent='aguardando 1º candle';}
  if(s.last_candle){const close=new Date(s.last_candle).getTime()+c.tf_secs*1000;
   $('cdl').textContent=dt(s.last_candle);
   $('cdl2').textContent='próxima checagem ~'+new Date(close+c.tf_secs*1000+90000).toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});}
  drawChart(eq);drawTrades(tr);
 }catch(e){$('upd').textContent='erro ao carregar: '+e.message}
}
function drawChart(eq){
 const empty=!eq.length;$('chEmpty').hidden=!empty;$('ch').hidden=empty;if(empty)return;
 const css=getComputedStyle(document.documentElement),col=n=>css.getPropertyValue(n).trim();
 const labels=eq.map(r=>dt(r.candle)),a=eq.map(r=>+r.equity),b=eq.map(r=>+r.bh_equity);
 if(chart){chart.data.labels=labels;chart.data.datasets[0].data=a;chart.data.datasets[1].data=b;chart.update('none');return}
 chart=new Chart($('ch'),{type:'line',data:{labels,datasets:[
  {label:'Estratégia',data:a,borderColor:col('--acc'),borderWidth:2,pointRadius:0,tension:.15},
  {label:'Comprar e segurar',data:b,borderColor:col('--bh'),borderWidth:1.5,borderDash:[5,4],pointRadius:0,tension:.15}]},
  options:{maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
   plugins:{legend:{labels:{color:col('--mut'),boxWidth:14}},tooltip:{callbacks:{label:x=>x.dataset.label+': '+usd(x.parsed.y)}}},
   scales:{x:{ticks:{color:col('--mut'),maxTicksLimit:8},grid:{display:false}},y:{ticks:{color:col('--mut'),callback:v=>usd(v)},grid:{color:col('--line')}}}}});
}
function drawTrades(tr){
 if(!tr.length){$('trades').innerHTML='<div class="empty">Nenhuma operação ainda.</div>';return}
 $('trades').innerHTML='<table><tr><th>Data</th><th>Lado</th><th>Preço</th><th>USDC</th><th>BTC</th></tr>'+
  tr.slice().reverse().map(t=>`<tr><td>${dt(t.time)}</td><td class="${t.side==='BUY'?'up':'down'}">${t.side==='BUY'?'Compra':'Venda'}</td><td>${usd(t.price)}</td><td>${usd(t.usdc)}</td><td>${(+t.btc).toFixed(6)}</td></tr>`).join('')+'</table>';
}
load();setInterval(load,60000);
</script></body></html>"""
