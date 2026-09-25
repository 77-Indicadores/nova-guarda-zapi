CHAT_HTML = """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nova Guarda Chat</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: #10251a;
      font-family: Arial, Helvetica, sans-serif;
      background: #eaf4ee;
    }
    .shell {
      width: min(1120px, calc(100vw - 28px));
      margin: 18px auto;
      border: 1px solid #7f8da6;
      background: #fff;
      box-shadow: 6px 6px 0 rgba(19, 32, 51, .16);
    }
    header {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 14px;
      color: #fff;
      background: linear-gradient(180deg, #23845a, #145a3b);
    }
    h1 { margin: 0; font-size: 18px; letter-spacing: 0; }
    .status { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; }
    .dot { width: 10px; height: 10px; border-radius: 50%; background: #47d16c; }
    main { display: grid; grid-template-columns: minmax(320px, 420px) 1fr; min-height: calc(100vh - 100px); }
    .composer, .events { padding: 16px; }
    .composer { border-right: 1px solid #b8c4d8; background: #f5f7fb; }
    .field { display: grid; gap: 6px; margin-bottom: 12px; }
    .grid-two { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    label { font-size: 13px; font-weight: 700; color: #344054; }
    input, textarea {
      width: 100%;
      border: 1px solid #98a6bd;
      border-radius: 4px;
      padding: 10px;
      color: #10251a;
      background: #fff;
      font: inherit;
    }
    textarea { min-height: 150px; resize: vertical; line-height: 1.4; }
    button {
      width: 100%;
      border: 1px solid #0f4c33;
      border-radius: 4px;
      padding: 11px 14px;
      color: #fff;
      background: linear-gradient(180deg, #2f9d6a, #17623f);
      font-weight: 700;
      cursor: pointer;
    }
    button:disabled { cursor: wait; opacity: .68; }
    .notice {
      margin-top: 12px;
      min-height: 42px;
      padding: 10px;
      border: 1px solid #b8c4d8;
      border-radius: 4px;
      background: #fff;
      color: #667085;
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .notice.ok { border-color: #9bd5bd; color: #1f8a5b; background: #f0fbf6; }
    .notice.error { border-color: #f0a6a0; color: #b42318; background: #fff4f2; }
    .status-card {
      margin-bottom: 12px;
      border: 1px solid #b8c4d8;
      border-radius: 6px;
      background: #fff;
      padding: 10px;
    }
    .status-card strong { display: block; margin-bottom: 4px; font-size: 13px; }
    .status-card span { color: #17623f; font-weight: 700; }
    .mode-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-bottom: 12px;
    }
    .mode-row input { position: absolute; opacity: 0; pointer-events: none; }
    .mode-row span {
      display: block;
      border: 1px solid #98a6bd;
      border-radius: 4px;
      padding: 9px 8px;
      background: #fff;
      color: #344054;
      font-size: 13px;
      font-weight: 700;
      text-align: center;
      cursor: pointer;
    }
    .mode-row input:checked + span {
      border-color: #17623f;
      color: #fff;
      background: #23845a;
    }
    .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
    .toolbar h2 { margin: 0; font-size: 16px; }
    .small-button { width: auto; padding: 7px 10px; font-size: 12px; background: #fff; color: #17623f; border-color: #b8c4d8; }
    .timeline { display: grid; gap: 10px; max-height: calc(100vh - 170px); overflow: auto; padding-right: 4px; align-content: start; }
    .event { max-width: 88%; border: 1px solid #b8c4d8; border-radius: 6px; background: #fff8df; overflow: hidden; }
    .event.received { justify-self: start; }
    .event.system { justify-self: center; max-width: 100%; background: #f7f9fc; }
    .event.sent { justify-self: end; background: #e6f6ed; }
    .event-head {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      padding: 8px 10px;
      color: #344054;
      background: #edf2f9;
      border-bottom: 1px solid #b8c4d8;
      font-size: 12px;
      font-weight: 700;
    }
    pre { margin: 0; padding: 10px; white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.45 Consolas, "Courier New", monospace; }
    .message-text { padding: 10px; line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; }
    .decision {
      display: inline-block;
      margin: 0 10px 10px;
      padding: 5px 8px;
      border-radius: 4px;
      background: #fff;
      border: 1px solid #b8c4d8;
      color: #17623f;
      font-size: 12px;
      font-weight: 700;
    }
    .map-link {
      display: inline-block;
      margin: 0 10px 10px;
      color: #17623f;
      font-size: 12px;
      font-weight: 700;
    }
    details summary { cursor: pointer; padding: 0 10px 10px; color: #667085; font-size: 12px; }
    .empty { padding: 28px 12px; color: #667085; text-align: center; border: 1px dashed #b8c4d8; background: #fff; }
    @media (max-width: 820px) {
      main { grid-template-columns: 1fr; }
      .composer { border-right: 0; border-bottom: 1px solid #b8c4d8; }
      header { align-items: flex-start; flex-direction: column; }
      .timeline { max-height: none; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <h1>Nova Guarda Chat</h1>
      <div class="status"><span class="dot"></span> online na porta {{ port }}</div>
    </header>
    <main>
      <section class="composer">
        <form id="send-form">
          <div class="mode-row">
            <label>
              <input type="radio" name="mode" value="text">
              <span>Texto livre</span>
            </label>
            <label>
              <input type="radio" name="mode" value="agenda" checked>
              <span>Agenda</span>
            </label>
            <label>
              <input type="radio" name="mode" value="checkin">
              <span>Check-in</span>
            </label>
            <label>
              <input type="radio" name="mode" value="checkin2">
              <span>Check-in 2</span>
            </label>
            <label>
              <input type="radio" name="mode" value="terms">
              <span>Aceite</span>
            </label>
          </div>

          <div class="field">
            <label for="phone">Telefone</label>
            <input id="phone" name="phone" inputmode="numeric" autocomplete="tel" placeholder="5511999999999" required>
          </div>
          <div class="field">
            <label for="client_name">Nome do cliente</label>
            <input id="client_name" name="client_name" placeholder="Felipe Varella">
          </div>
          <div class="field">
            <label for="client_address">Endereço do cliente</label>
            <input id="client_address" name="client_address" placeholder="Rua Exemplo, 123 - Santos/SP">
          </div>
          <div class="grid-two">
            <div class="field">
              <label for="schedule_date">Data</label>
              <input id="schedule_date" name="schedule_date" placeholder="20/05/2026">
            </div>
            <div class="field">
              <label for="schedule_time">Horário</label>
              <input id="schedule_time" name="schedule_time" placeholder="15:00">
            </div>
          </div>
          <div class="field">
            <label for="service">Serviço</label>
            <input id="service" name="service" value="Atendimento da cooperativa">
          </div>
          <div class="field">
            <label for="message">Mensagem livre ou prévia da agenda</label>
            <textarea id="message" name="message" placeholder="Digite a mensagem..." required></textarea>
          </div>
          <button id="send-button" type="submit">Enviar mensagem</button>
        </form>
        <div id="notice" class="notice">Pronto para enviar pela Z-API.</div>
      </section>
      <section class="events">
        <div class="status-card">
          <strong>Status do fluxo</strong>
          <span id="agenda-status">Aguardando envio</span>
        </div>
        <div class="toolbar">
          <h2>Histórico da conversa</h2>
          <button class="small-button" id="refresh-button" type="button">Atualizar</button>
        </div>
        <div id="timeline" class="timeline"></div>
      </section>
    </main>
  </div>
  <script>
    const form = document.querySelector("#send-form");
    const button = document.querySelector("#send-button");
    const notice = document.querySelector("#notice");
    const timeline = document.querySelector("#timeline");
    const refreshButton = document.querySelector("#refresh-button");
    const phoneInput = document.querySelector("#phone");
    const messageInput = document.querySelector("#message");
    const agendaStatus = document.querySelector("#agenda-status");
    const agendaFields = ["client_name", "client_address", "schedule_date", "schedule_time", "service"]
      .map((name) => document.querySelector(`[name="${name}"]`));
    const savedPhone = localStorage.getItem("novaGuardaPhone");
    if (savedPhone) phoneInput.value = savedPhone;
    const savedAgenda = JSON.parse(localStorage.getItem("novaGuardaAgenda") || "{}");
    agendaFields.forEach((field) => {
      if (savedAgenda[field.name]) field.value = savedAgenda[field.name];
    });

    function escapeHtml(value) {
      return value.replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }[char]));
    }

    function setNotice(text, kind = "") {
      notice.className = `notice ${kind}`.trim();
      notice.textContent = text;
    }

    function getMessageText(payload) {
      return payload?.text?.message || payload?.buttonReply?.message || payload?.buttonsResponseMessage?.selectedDisplayText || payload?.listResponseMessage?.title || "";
    }

    function getDecision(text) {
      const normalized = String(text || "").trim().toLowerCase();
      if (["1", "confirmar", "confirmado", "confirmada"].includes(normalized)) return "Agenda confirmada";
      if (["2", "reagendar", "remarcar"].includes(normalized)) return "Solicitou reagendamento";
      if (["3", "cancelar", "cancelado", "cancelada"].includes(normalized)) return "Agenda cancelada";
      if (["sim, cheguei", "cheguei", "sim"].includes(normalized)) return "Chegou ao local";
      if (["vou atrasar", "atrasarei", "atrasar"].includes(normalized)) return "Vai atrasar";
      if (["15 minutos", "15 min"].includes(normalized)) return "Vai atrasar 15 minutos";
      if (["30 minutos", "30 min"].includes(normalized)) return "Vai atrasar 30 minutos";
      if (["1 hora", "60 minutos", "60 min"].includes(normalized)) return "Vai atrasar 1 hora";
      if (["não vou", "nao vou", "não irei", "nao irei"].includes(normalized)) return "Não vai comparecer";
      if (["problema pessoal"].includes(normalized)) return "Motivo: problema pessoal";
      if (["sem acesso ao local"].includes(normalized)) return "Motivo: sem acesso ao local";
      if (["cliente cancelou"].includes(normalized)) return "Motivo: cliente cancelou";
      if (["outro motivo"].includes(normalized)) return "Motivo: outro motivo";
      return "";
    }

    function buildAgendaPreview() {
      const data = Object.fromEntries(agendaFields.map((field) => [field.name, field.value.trim()]));
      const client = data.client_name || "cooperado(a)";
      const address = data.client_address || "Endereço não informado";
      const date = data.schedule_date || "Data não informada";
      const time = data.schedule_time || "Horário não informado";
      const service = data.service || "Atendimento Nova Guarda";

      return `Olá, ${client}. Aqui é a Nova Guarda.

Sua escala está disponível para confirmação:

Endereço: ${address}
Data: ${date}
Horário: ${time}
Atendimento: ${service}

Confirme abaixo se você poderá atender esta escala.`;
    }

    function buildCheckinPreview() {
      const data = Object.fromEntries(agendaFields.map((field) => [field.name, field.value.trim()]));
      const client = data.client_name || "cooperado(a)";
      const address = data.client_address || "Endereço não informado";
      const date = data.schedule_date || "Data não informada";
      const time = data.schedule_time || "Horário não informado";
      const service = data.service || "Atendimento Nova Guarda";

      return `Olá, ${client}. Aqui é a Nova Guarda.

Está na hora de confirmar sua chegada para o atendimento:

Endereço: ${address}
Data: ${date}
Horário: ${time}
Atendimento: ${service}

Selecione a opção que corresponde à sua situação agora.`;
    }

    function buildCheckin2Preview() {
      return buildCheckinPreview();
    }

    function buildTermsPreview() {
      const data = Object.fromEntries(agendaFields.map((field) => [field.name, field.value.trim()]));
      const client = data.client_name || "cooperado(a)";
      return `Olá, ${client}. Aqui é a Nova Guarda.

Para receber avisos de escala, check-in e orientações operacionais por este WhatsApp, precisamos do seu aceite no termo de uso e consentimento.

Leia o termo enviado e escolha uma das opções abaixo. Seu aceite ficará registrado pela Nova Guarda.`;
    }

    function syncMessagePreview() {
      if (form.mode.value === "agenda") {
        messageInput.value = buildAgendaPreview();
      } else if (form.mode.value === "checkin") {
        messageInput.value = buildCheckinPreview();
      } else if (form.mode.value === "checkin2") {
        messageInput.value = buildCheckin2Preview();
      } else if (form.mode.value === "terms") {
        messageInput.value = buildTermsPreview();
      } else {
        messageInput.value = "";
      }
    }

    function readAgendaData() {
      return Object.fromEntries(agendaFields.map((field) => [field.name, field.value.trim()]));
    }

    function renderEvents(events) {
      if (!events.length) {
        timeline.innerHTML = '<div class="empty">Nenhuma resposta recebida ainda.</div>';
        return;
      }

      timeline.innerHTML = events.map((event) => {
        const payload = event.payload || {};
        const isSent = payload.type === "SentMessage";
        const isMessage = payload.type === "ReceivedCallback" || isSent;
        const text = getMessageText(payload);
        const location = payload.location || null;
        const decision = getDecision(text);
        const title = isSent
          ? `Enviado para ${payload.phone || "contato"}`
          : isMessage
          ? `${payload.chatName || payload.senderName || payload.phone || "Contato"}`
          : `${payload.type || "Evento"}`;
        const mapsUrl = location?.latitude && location?.longitude
          ? `https://www.google.com/maps?q=${location.latitude},${location.longitude}`
          : location?.url || "";
        const body = location
          ? `Localização recebida
Latitude: ${location.latitude ?? ""}
Longitude: ${location.longitude ?? ""}
Endereço: ${location.address || ""}`
          : isMessage && text ? text : JSON.stringify(payload, null, 2);

        return `
          <article class="event ${isSent ? "sent" : isMessage ? "received" : "system"}">
            <div class="event-head"><span>${escapeHtml(event.received_at)}</span><span>${escapeHtml(title)}</span></div>
            <div class="message-text">${escapeHtml(body)}</div>
            ${decision ? `<span class="decision">${decision}</span>` : ""}
            ${mapsUrl ? `<a class="map-link" href="${escapeHtml(mapsUrl)}" target="_blank" rel="noreferrer">Abrir no mapa</a>` : ""}
            <details>
              <summary>Ver payload completo</summary>
              <pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>
            </details>
          </article>
        `;
      }).join("");

      const latestDecision = events.find((event) => getDecision(getMessageText(event.payload || {})));
      if (latestDecision) {
        agendaStatus.textContent = getDecision(getMessageText(latestDecision.payload || {}));
      }
    }

    async function loadEvents() {
      const response = await fetch("/api/events");
      const data = await response.json();
      renderEvents(data.events || []);
      await loadAgendaStatus();
    }

    async function loadAgendaStatus() {
      const phone = phoneInput.value.replace(/\\D/g, "");
      if (!phone) {
        agendaStatus.textContent = "Aguardando envio";
        return;
      }

      if (form.mode.value === "terms") {
        const response = await fetch(`/api/terms-status?phone=${phone}`);
        const data = await response.json();
        agendaStatus.textContent = data.status_label || "Aceite não enviado";
        return;
      }

      const response = await fetch(`/api/agenda-status?phone=${phone}`);
      const data = await response.json();
      agendaStatus.textContent = data.status_label || "Aguardando envio";
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      button.disabled = true;
      setNotice("Enviando...");

      const phone = form.phone.value.replace(/\\D/g, "");
      const message = form.message.value.trim();
      const mode = form.mode.value;
      const agendaData = readAgendaData();
      localStorage.setItem("novaGuardaPhone", phone);
      localStorage.setItem("novaGuardaAgenda", JSON.stringify(agendaData));

      try {
        const response = await fetch("/api/send-message", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ phone, message, mode, ...agendaData }),
        });
        const data = await response.json();
        if (!response.ok || !data.ok) {
          throw new Error(data.error || "Falha ao enviar mensagem.");
        }
        const label = mode === "agenda"
          ? "Mensagem com botões enviada"
          : mode === "checkin"
          ? "Check-in enviado"
          : mode === "terms"
          ? "Termo enviado"
          : "Mensagem enviada";
        setNotice(`${label}. ID: ${data.response.messageId || data.response.id || "sem id"}`, "ok");
        if (mode === "agenda" || mode === "checkin" || mode === "terms") {
          agendaStatus.textContent = mode === "checkin" ? "Check-in enviado" : mode === "terms" ? "Aguardando aceite" : "Aguardando resposta";
        }
        if (mode !== "agenda") {
          messageInput.value = "";
        }
      } catch (error) {
        setNotice(error.message, "error");
      } finally {
        button.disabled = false;
      }
    });

    refreshButton.addEventListener("click", loadEvents);
    agendaFields.forEach((field) => field.addEventListener("input", syncMessagePreview));
    document.querySelectorAll('[name="mode"]').forEach((field) => field.addEventListener("change", syncMessagePreview));
    syncMessagePreview();
    loadEvents();
    setInterval(loadEvents, 5000);
  </script>
</body>
</html>
"""


LOCATION_HTML = """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Confirmar localização</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #eaf4ee;
      color: #10251a;
      font-family: Arial, Helvetica, sans-serif;
    }
    .box {
      width: min(440px, calc(100vw - 28px));
      border: 1px solid #7f8da6;
      border-radius: 6px;
      background: #fff;
      padding: 18px;
      box-shadow: 6px 6px 0 rgba(19, 32, 51, .16);
    }
    h1 { margin: 0 0 10px; font-size: 20px; }
    p { color: #667085; line-height: 1.45; }
    button {
      width: 100%;
      border: 1px solid #0f4c33;
      border-radius: 4px;
      padding: 12px 14px;
      color: #fff;
      background: linear-gradient(180deg, #2f9d6a, #17623f);
      font-weight: 700;
      cursor: pointer;
    }
    .status { margin-top: 12px; color: #17623f; font-weight: 700; }
  </style>
</head>
<body>
  <div class="box">
    <h1>Confirmar localização</h1>
    <p>Toque no botão abaixo e permita o acesso à localização para concluir o check-in.</p>
    <button id="confirm-button">Enviar minha localização</button>
    <div id="status" class="status"></div>
  </div>
  <script>
    const button = document.querySelector("#confirm-button");
    const statusBox = document.querySelector("#status");

    button.addEventListener("click", () => {
      statusBox.textContent = "Solicitando localização...";
      if (!navigator.geolocation) {
        statusBox.textContent = "Seu navegador não suporta localização.";
        return;
      }

      navigator.geolocation.getCurrentPosition(async (position) => {
        statusBox.textContent = "Enviando localização...";
        const response = await fetch(window.location.pathname, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
            accuracy: position.coords.accuracy,
          }),
        });
        const data = await response.json();
        statusBox.textContent = data.message || "Localização enviada. Obrigado!";
        button.disabled = true;
      }, () => {
        statusBox.textContent = "Não foi possível acessar sua localização. Verifique a permissão do navegador.";
      }, {
        enableHighAccuracy: true,
        timeout: 15000,
        maximumAge: 0,
      });
    });
  </script>
</body>
</html>
"""


DEV_CHAT_HTML = """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WhatsApp Local</title>
  <style>
    * { box-sizing: border-box; }
    :root {
      --wa-green: #075e54;
      --wa-green-2: #128c7e;
      --wa-light: #dcf8c6;
      --wa-bg: #efe7dd;
      --wa-line: #d8cec2;
      --ink: #111b21;
      --muted: #667781;
    }
    body {
      margin: 0;
      min-height: 100vh;
      background: #d9dbd5;
      color: var(--ink);
      font-family: Arial, Helvetica, sans-serif;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0 0 auto;
      height: 128px;
      background: var(--wa-green);
    }
    .phone {
      position: relative;
      width: min(430px, 100vw);
      height: min(860px, 100vh);
      margin: 0 auto;
      display: grid;
      grid-template-rows: auto 1fr auto;
      background: var(--wa-bg);
      box-shadow: 0 16px 48px rgba(17, 27, 33, .22);
      overflow: hidden;
    }
    .topbar {
      min-height: 64px;
      display: grid;
      grid-template-columns: 44px 42px 1fr auto;
      align-items: center;
      gap: 8px;
      padding: 8px 10px;
      color: #fff;
      background: var(--wa-green);
    }
    .icon {
      width: 40px;
      height: 40px;
      border: 0;
      border-radius: 50%;
      display: grid;
      place-items: center;
      color: inherit;
      background: transparent;
      font: inherit;
      cursor: pointer;
    }
    .avatar {
      width: 40px;
      height: 40px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      background: #dfe5e7;
      color: var(--wa-green);
      font-weight: 700;
    }
    .person { min-width: 0; }
    .person strong {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 16px;
      line-height: 1.2;
    }
    .person span { display: block; margin-top: 2px; font-size: 12px; color: rgba(255,255,255,.8); }
    .actions { display: flex; gap: 2px; }
    .chat {
      min-height: 0;
      overflow: auto;
      padding: 12px 10px 16px;
      display: grid;
      gap: 6px;
      align-content: start;
      background:
        radial-gradient(circle at 20px 20px, rgba(0,0,0,.025) 0 1px, transparent 1px 20px),
        var(--wa-bg);
    }
    .day {
      justify-self: center;
      margin: 4px 0 8px;
      padding: 6px 12px;
      border-radius: 7px;
      background: #e1f2fb;
      color: #54656f;
      font-size: 12px;
      box-shadow: 0 1px 1px rgba(17, 27, 33, .12);
    }
    .bubble {
      position: relative;
      max-width: 82%;
      min-width: 86px;
      padding: 7px 8px 18px;
      border-radius: 8px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 14px;
      line-height: 1.38;
      box-shadow: 0 1px 1px rgba(17, 27, 33, .16);
    }
    .incoming { justify-self: start; background: #fff; border-top-left-radius: 2px; }
    .outgoing { justify-self: end; background: var(--wa-light); border-top-right-radius: 2px; }
    .system {
      justify-self: center;
      max-width: 92%;
      min-width: 0;
      padding: 7px 10px;
      border-radius: 7px;
      background: #fff7d6;
      color: #54656f;
      font-size: 12px;
      text-align: center;
    }
    .time {
      position: absolute;
      right: 8px;
      bottom: 4px;
      color: rgba(84, 101, 111, .88);
      font-size: 11px;
      line-height: 1;
    }
    .ticks { color: #53bdeb; margin-left: 3px; }
    .reply-panel {
      border-top: 1px solid rgba(0,0,0,.06);
      background: #f0f2f5;
    }
    .message-actions {
      display: grid;
      gap: 1px;
      margin: 8px -8px -18px;
      padding-bottom: 18px;
      border-radius: 8px;
      overflow: hidden;
      border-top: 1px solid rgba(0,0,0,.08);
    }
    .message-actions button {
      min-height: 42px;
      border: 0;
      border-top: 1px solid #e5ebef;
      padding: 10px 12px;
      background: #fff;
      color: var(--wa-green-2);
      font-weight: 700;
      cursor: pointer;
      text-align: center;
    }
    .message-actions button:first-child { border-top: 0; }
    .composer {
      display: grid;
      grid-template-columns: 1fr 46px;
      gap: 8px;
      align-items: end;
      padding: 8px 10px 10px;
    }
    .messagebox {
      min-height: 44px;
      display: grid;
      grid-template-columns: 38px 1fr 38px;
      align-items: end;
      border-radius: 22px;
      background: #fff;
      padding: 0 4px 0 2px;
    }
    textarea {
      width: 100%;
      min-height: 42px;
      max-height: 120px;
      border: 0;
      outline: 0;
      resize: none;
      padding: 12px 2px 9px;
      font: inherit;
      line-height: 1.35;
      color: var(--ink);
      background: transparent;
    }
    .send {
      width: 46px;
      height: 46px;
      border: 0;
      border-radius: 50%;
      display: grid;
      place-items: center;
      color: #fff;
      background: var(--wa-green-2);
      cursor: pointer;
    }
    .setup {
      position: absolute;
      right: 10px;
      top: 72px;
      width: min(300px, calc(100% - 20px));
      display: none;
      padding: 12px;
      border-radius: 8px;
      background: #fff;
      box-shadow: 0 12px 32px rgba(17, 27, 33, .24);
      z-index: 3;
    }
    .setup.open { display: block; }
    label { display: grid; gap: 5px; margin-bottom: 8px; color: var(--muted); font-size: 12px; font-weight: 700; }
    input {
      width: 100%;
      border: 1px solid #c8d0d5;
      border-radius: 5px;
      padding: 9px;
      color: var(--ink);
      font: inherit;
    }
    .coord { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .dev-start {
      display: grid;
      gap: 8px;
      margin-top: 8px;
      padding-top: 10px;
      border-top: 1px solid #e5ebef;
    }
    .dev-start button {
      min-height: 38px;
      border: 1px solid #c8d0d5;
      border-radius: 6px;
      background: #fff;
      color: var(--wa-green-2);
      font-weight: 700;
      cursor: pointer;
    }
    @media (min-width: 760px) {
      body { padding: 24px 0; }
      .phone { border-radius: 12px; height: min(820px, calc(100vh - 48px)); }
    }
  </style>
</head>
<body>
  <div class="phone">
    <header class="topbar">
      <button class="icon" type="button" aria-label="Voltar">‹</button>
      <div class="avatar">CT</div>
      <div class="person">
        <strong id="chat-name">Cooperado Teste</strong>
        <span>online</span>
      </div>
      <div class="actions">
        <button class="icon" type="button" aria-label="Enviar localização" id="send-location">⌖</button>
        <button class="icon" type="button" aria-label="Configurar contato" id="settings">⋮</button>
      </div>
    </header>

    <section class="setup" id="setup">
      <label>Telefone
        <input id="phone" value="5513981439990" inputmode="numeric">
      </label>
      <label>Nome
        <input id="name" value="Cooperado Teste">
      </label>
      <div class="coord">
        <label>Latitude
          <input id="lat" value="-23.9608">
        </label>
        <label>Longitude
          <input id="lng" value="-46.3336">
        </label>
      </div>
      <div class="dev-start">
        <button type="button" data-start-flow="agenda">Iniciar agenda</button>
        <button type="button" data-start-flow="checkin2">Iniciar check-in</button>
        <button type="button" data-start-flow="terms">Iniciar aceite</button>
      </div>
    </section>

    <main id="timeline" class="chat"></main>

    <section class="reply-panel">
      <form id="form" class="composer">
        <div class="messagebox">
          <button class="icon" type="button" aria-label="Abrir opções">＋</button>
          <textarea id="message" rows="1" placeholder="Mensagem"></textarea>
          <button class="icon" type="button" aria-label="Anexar">⌕</button>
        </div>
        <button class="send" type="submit" aria-label="Enviar">➤</button>
      </form>
    </section>
  </div>

  <script>
    const timeline = document.querySelector("#timeline");
    const form = document.querySelector("#form");
    const message = document.querySelector("#message");
    const phone = document.querySelector("#phone");
    const nameInput = document.querySelector("#name");
    const chatName = document.querySelector("#chat-name");
    const lat = document.querySelector("#lat");
    const lng = document.querySelector("#lng");
    const setup = document.querySelector("#setup");

    function escapeHtml(value) {
      return String(value || "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      }[char]));
    }

    function eventTime(value) {
      const parts = String(value || "").split(" ");
      return parts[1] ? parts[1].slice(0, 5) : "";
    }

    function payloadText(payload) {
      return payload?.text?.message
        || payload?.buttonReply?.message
        || payload?.buttonsResponseMessage?.selectedDisplayText
        || payload?.listResponseMessage?.title
        || "";
    }

    function isMine(payload) {
      return payload.type === "SentMessage" || payload.type === "AutoReply";
    }

    function payloadActions(payload) {
      const directButtons = Array.isArray(payload.buttons) ? payload.buttons : [];
      const directOptions = Array.isArray(payload.options) ? payload.options : [];
      const responsePayload = payload?.response?.payload || {};
      const responseButtons = Array.isArray(responsePayload.buttons) ? responsePayload.buttons : [];
      const responseOptions = Array.isArray(responsePayload.options) ? responsePayload.options : [];
      const seen = new Set();
      return [...directButtons, ...directOptions, ...responseButtons, ...responseOptions]
        .map((item) => ({
          id: item.id || item.title || item.label,
          label: item.label || item.title || item.id,
        }))
        .filter((item) => {
          if (!item.id || !item.label || seen.has(item.id)) return false;
          seen.add(item.id);
          return true;
        });
    }

    async function loadEvents() {
      const res = await fetch("/api/events");
      const data = await res.json();
      const events = (data.events || []).slice().reverse();
      const currentPhone = phone.value.replace(/\\D/g, "");
      const filtered = events.filter((event) => {
        const payload = event.payload || {};
        return !payload.phone || !currentPhone || String(payload.phone).replace(/\\D/g, "") === currentPhone;
      });

      timeline.innerHTML = '<div class="day">Hoje</div>' + filtered.map((event) => {
        const payload = event.payload || {};
        const text = payload.location
          ? `Localização compartilhada\\n${payload.location.address || ""}\\n${payload.location.latitude || ""}, ${payload.location.longitude || ""}`
          : payloadText(payload) || (payload.type ? `Evento: ${payload.type}` : JSON.stringify(payload, null, 2));
        const kind = payload.type === "LocationLinkCallback" ? "system" : isMine(payload) ? "outgoing" : payload.type === "ReceivedCallback" ? "incoming" : "system";
        const ticks = kind === "outgoing" ? '<span class="ticks">✓✓</span>' : "";
        const actions = kind === "outgoing" ? payloadActions(payload) : [];
        const buttons = actions.length
          ? `<div class="message-actions">${actions.map((action) => `<button type="button" data-reply="${escapeHtml(action.id)}">${escapeHtml(action.label)}</button>`).join("")}</div>`
          : "";
        return `<article class="bubble ${kind}">${escapeHtml(text)}${buttons}${kind === "system" ? "" : `<span class="time">${escapeHtml(eventTime(event.received_at))}${ticks}</span>`}</article>`;
      }).join("");
      timeline.scrollTop = timeline.scrollHeight;
      timeline.querySelectorAll("[data-reply]").forEach((button) => {
        button.addEventListener("click", () => sendIncoming(button.dataset.reply));
      });
    }

    async function sendIncoming(text, location = null) {
      const body = { phone: phone.value, chatName: nameInput.value, message: text, location };
      const res = await fetch("/dev/simulate-whatsapp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) alert((await res.json()).error || "Falha ao simular mensagem.");
      message.value = "";
      await loadEvents();
    }

    async function startFlow(mode) {
      const res = await fetch("/dev/start-flow", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          phone: phone.value,
          mode,
          client_name: nameInput.value || "Cooperado Teste",
          client_address: "Rua Exemplo, 123 - Santos/SP",
          schedule_date: "20/05/2026",
          schedule_time: "15:00",
          service: "Atendimento da cooperativa"
        }),
      });
      if (!res.ok) alert((await res.json()).error || "Falha ao iniciar fluxo.");
      setup.classList.remove("open");
      await loadEvents();
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = message.value.trim();
      if (text) sendIncoming(text);
    });

    message.addEventListener("input", () => {
      message.style.height = "auto";
      message.style.height = `${message.scrollHeight}px`;
    });

    nameInput.addEventListener("input", () => {
      chatName.textContent = nameInput.value || "Cooperado Teste";
    });

    document.querySelector("#settings").addEventListener("click", () => {
      setup.classList.toggle("open");
    });

    document.querySelectorAll("[data-start-flow]").forEach((button) => {
      button.addEventListener("click", () => startFlow(button.dataset.startFlow));
    });

    document.querySelector("#send-location").addEventListener("click", () => {
      sendIncoming("", {
        latitude: Number(lat.value),
        longitude: Number(lng.value),
        address: "Localização fake do dev"
      });
    });

    loadEvents();
    setInterval(loadEvents, 2500);
  </script>
</body>
</html>
"""
