// openbanking-hub.js
import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import fs from 'fs';
import fetch from 'node-fetch';
import http from 'http';
import { WebSocketServer } from 'ws';

const {
  PORT = '4000',
  AGENT_WEBHOOK = 'http://localhost:8000/webhook/bank',
  SERVICE_TOKEN = 'secret'
} = process.env;

const app = express();
app.use(cors());
app.use(express.json({ limit: '1mb' }));

// --- Load users
let USERS = {};
try {
  USERS = JSON.parse(fs.readFileSync('./data/users.json', 'utf-8'));
  console.log("✅ Loaded users.json");
} catch (e) {
  console.error("❌ Failed to load users.json", e.message);
}

// --- In-memory stores
const OTP_STORE = new Map();   // key = phone:bank:accountId
const TOKEN_STORE = new Map(); // key = phone:bank:accountId

function genOTP() {
  return String(Math.floor(100000 + Math.random() * 900000));
}

// --- Push tới AI Agent qua webhook (giữ nguyên để Agent auto-resume)
async function notifyAgent(event, phone, bank, payload = {}) {
  try {
    await fetch(AGENT_WEBHOOK, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${SERVICE_TOKEN}`
      },
      body: JSON.stringify({ event, phone, bank, payload })
    });
  } catch (e) {
    console.error("❌ Webhook error:", e.message);
  }
}

// ---------- WEBSOCKET (giả lập SMS) ----------
/**
 * WS endpoint: ws://localhost:4000/ws?phone=demo:thao
 * - mỗi kết nối đăng ký theo phone
 * - server sẽ phát sự kiện OTP/thông báo theo từng phone
 */
const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

// phone -> Set<WebSocket>
const WS_CHANNELS = new Map();

function addSocket(phone, ws) {
  if (!WS_CHANNELS.has(phone)) WS_CHANNELS.set(phone, new Set());
  WS_CHANNELS.get(phone).add(ws);
}

function removeSocket(phone, ws) {
  const set = WS_CHANNELS.get(phone);
  if (!set) return;
  set.delete(ws);
  if (!set.size) WS_CHANNELS.delete(phone);
}

function emitTo(phone, event, payload) {
  const set = WS_CHANNELS.get(phone);
  if (!set) return;
  const msg = JSON.stringify({ event, phone, payload, ts: Date.now() });
  for (const ws of set) {
    try { ws.send(msg); } catch (_) {}
  }
}

wss.on('connection', (ws, req) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const phone = url.searchParams.get('phone');
  if (!phone) {
    ws.close(1008, 'phone required');
    return;
  }
  addSocket(phone, ws);

  ws.on('close', () => removeSocket(phone, ws));
  ws.on('error', () => removeSocket(phone, ws));

  // optional: cho client biết đã join
  ws.send(JSON.stringify({ event: 'ws_connected', phone, ts: Date.now() }));
});
// ---------------------------------------------

// Health check
app.get('/health', (_, res) => {
  const banks = new Set();
  Object.values(USERS).forEach(u => Object.keys(u).forEach(b => banks.add(b)));
  res.json({ ok: true, banks: [...banks], total_users: Object.keys(USERS).length });
});

// List accounts of a user in a bank
app.get('/bank/:bank/accounts/:phone', (req, res) => {
  const { phone, bank } = req.params;
  const accounts = USERS[phone]?.[bank] || [];
  res.json(accounts);
});

// Request OTP
app.post('/bank/:bank/balance', async (req, res) => {
  const { phone, account_id } = req.body || {};
  if (!phone || !account_id) return res.status(400).json({ error: 'phone and account_id required' });

  const key = `${phone}:${req.params.bank}:${account_id}`;
  const code = genOTP();
  OTP_STORE.set(key, { code, exp: Date.now() + 5 * 60 * 1000 });

  console.log(`OTP for ${key}: ${code}`);

  // 1) Webhook cho Agent
  await notifyAgent("otp_sent", phone, req.params.bank, {
    text: `Ngân hàng ${req.params.bank} đã gửi mã OTP ${code} tới ${phone} cho tài khoản ${account_id}`,
    otp: code,
    account_id
  });

  // 2) WebSocket cho UI (giả lập SMS tới đúng phone)
  emitTo(phone, "otp_sent", {
    bank: req.params.bank,
    text: `OTP: ${code} cho tài khoản ${account_id}`,
    otp: code,
    account_id,
    expires_in: 300
  });

  res.json({ message: "OTP đã gửi (demo). Hết hạn sau 5 phút.", expires_in: 300 });
});

// Verify OTP
app.post('/bank/:bank/otp/verify', async (req, res) => {
  const { phone, otp, account_id } = req.body || {};
  const key = `${phone}:${req.params.bank}:${account_id}`;
  const rec = OTP_STORE.get(key);

  if (!rec || Date.now() > rec.exp) {
    emitTo(phone, "otp_failed", { reason: "expired", account_id, bank: req.params.bank });
    return res.json({ success: false, reason: 'expired' });
  }
  if (otp !== rec.code) {
    emitTo(phone, "otp_failed", { reason: "wrong", account_id, bank: req.params.bank });
    return res.json({ success: false, reason: 'wrong' });
  }

  OTP_STORE.delete(key);
  const token = `token_${key}_${Date.now()}`;
  TOKEN_STORE.set(key, { token, exp: Date.now() + 600000 });

  const account = USERS[phone]?.[req.params.bank]?.find(a => a.accountId === account_id);
  const summary = {
    account_number: account?.accountId,
    balance: account?.balance,
    transactions: account?.transactions || [],
    last_update: account?.last_update
  };

  // 1) Webhook cho Agent (để Agent auto-resume)
  await notifyAgent("otp_verified", phone, req.params.bank, {
    access_token: token,
    ttl: 600,
    account_id,
    account_summary: summary
  });

  // 2) WebSocket cho UI
  emitTo(phone, "otp_verified", {
    bank: req.params.bank,
    account_id,
    access_token: token,
    ttl: 600,
    account_summary: summary
  });

  res.json({
    success: true,
    access_token: token,
    ttl: 600,
    account_summary: summary
  });
});

// Banking products
app.get('/bank/:bank/services', (req, res) => {
  const catalog = [
    { title: 'Vay mua nhà lãi suất linh hoạt' },
    { title: 'Tiết kiệm bậc thang 12 tháng' },
    { title: 'Thẻ tín dụng hoàn tiền 5%' }
  ];
  const q = (req.query.query || "").toLowerCase();
  res.json(catalog.filter(s => s.title.toLowerCase().includes(q)));
});

server.listen(Number(PORT), () => {
  console.log(`✅ Open Banking Hub running at http://localhost:${PORT}`);
  console.log(`🔌 WS endpoint: ws://localhost:${PORT}/ws?phone=<your-phone>`);
});
