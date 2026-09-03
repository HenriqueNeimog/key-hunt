"use strict";

const cookieValue = (name) => {
  const found = document.cookie.split("; ").find((entry) => entry.startsWith(`${name}=`));
  return found ? decodeURIComponent(found.split("=").slice(1).join("=")) : "";
};

const api = async (url, options = {}) => {
  const headers = new Headers(options.headers || {});
  if (options.method && options.method !== "GET") headers.set("X-CSRF-Token", cookieValue("key_hunt_csrf"));
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(url, { credentials: "same-origin", ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Algo deu errado. Tente novamente.");
  return data;
};

const text = (element, value) => { if (element) element.textContent = String(value); };

const setupHome = () => {
  const form = document.querySelector("#playlist-form");
  if (!form || form.dataset.initialized) return;
  form.dataset.initialized = "true";
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.querySelector("#playlist-url");
    const message = document.querySelector("#form-message");
    const button = form.querySelector("button");
    message.classList.remove("error");
    text(message, "Importando os metadados da playlist…");
    form.classList.add("busy");
    button.disabled = true;
    try {
      const data = await api("/api/playlists/import", { method: "POST", body: JSON.stringify({ url: input.value }) });
      window.location.assign(`/game/${encodeURIComponent(data.round_id)}`);
    } catch (error) {
      message.classList.add("error");
      text(message, error.message);
      form.classList.remove("busy");
      button.disabled = false;
    }
  });
};

const labels = {
  queued: ["Na fila", "Aguardando processamento…"],
  downloading: ["Preparando", "Extraindo o áudio com segurança…"],
  audio_ready: ["Áudio pronto", "Você já pode ouvir. Iniciando análise…"],
  analyzing: ["Analisando", "O player está disponível enquanto analisamos…"],
  ready: ["Pronto", "Ouça, decida e revele quando quiser."],
  failed: ["Falha", "Não foi possível preparar esta faixa."],
};

const setupGame = () => {
  const shell = document.querySelector(".game-shell");
  if (!shell || shell.dataset.initialized) return;
  shell.dataset.initialized = "true";
  const roundId = shell.dataset.roundId;
  let playlistId = "";
  let timer = null;
  const elements = {
    badge: document.querySelector("#status-badge"), processing: document.querySelector("#processing-text"),
    title: document.querySelector("#track-title"), artist: document.querySelector("#track-artist"),
    cover: document.querySelector("#cover"), thumbnail: document.querySelector("#thumbnail"),
    playerWrap: document.querySelector("#player-wrap"), player: document.querySelector("#audio-player"),
    playerToggle: document.querySelector("#player-toggle"),
    currentTime: document.querySelector("#current-time"),
    durationTime: document.querySelector("#duration-time"),
    playerVolume: document.querySelector("#player-volume"),
    reveal: document.querySelector("#reveal-button"), message: document.querySelector("#game-message"),
    answerPanel: document.querySelector("#answer-panel"), answer: document.querySelector("#answer"),
    confidence: document.querySelector("#confidence-note"), nextArea: document.querySelector("#next-area"),
    next: document.querySelector("#next-button"),
  };
  const formatTime = (seconds) => {
    if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
    const minutes = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${minutes}:${String(secs).padStart(2, "0")}`;
  };
  const syncPlayerUi = () => {
    if (!elements.player || !elements.playerToggle) return;
    elements.playerToggle.textContent = elements.player.paused ? "▶" : "❚❚";
    elements.playerToggle.classList.toggle("is-playing", !elements.player.paused);
  };
  elements.playerToggle?.addEventListener("click", () => {
    if (!elements.player || !elements.player.src) return;
    if (elements.player.paused) {
      elements.player.play();
    } else {
      elements.player.pause();
    }
  });
  elements.playerVolume?.addEventListener("input", (event) => {
    if (!elements.player) return;
    elements.player.volume = Number(event.target.value);
  });
  elements.player?.addEventListener("loadedmetadata", () => {
    text(elements.durationTime, formatTime(elements.player.duration || 0));
    elements.player.volume = Number(elements.playerVolume?.value || 0.8);
  });
  elements.player?.addEventListener("timeupdate", () => {
    text(elements.currentTime, formatTime(elements.player.currentTime || 0));
  });
  elements.player?.addEventListener("play", syncPlayerUi);
  elements.player?.addEventListener("pause", syncPlayerUi);
  elements.player?.addEventListener("ended", syncPlayerUi);
  const poll = async () => {
    try {
      const data = await api(`/api/rounds/${encodeURIComponent(roundId)}/status`);
      playlistId = data.playlist_id;
      text(elements.title, data.track.title);
      text(elements.artist, data.track.artist || "Artista não informado");
      elements.title.classList.remove("skeleton-text");
      elements.cover.classList.remove("skeleton");
      if (data.track.thumbnail_url) {
        elements.thumbnail.src = data.track.thumbnail_url;
        elements.thumbnail.hidden = false;
        elements.thumbnail.onerror = () => { elements.thumbnail.hidden = true; };
      }
      const label = labels[data.status] || labels.queued;
      text(elements.badge, label[0]);
      text(elements.processing, data.error || label[1]);
      if (data.audio_url && !elements.player.src) {
        elements.player.src = data.audio_url;
        elements.player.currentTime = 0;
        elements.playerWrap.hidden = false;
        text(elements.currentTime, "0:00");
        text(elements.durationTime, "0:00");
        syncPlayerUi();
      }
      elements.reveal.disabled = !data.can_reveal;
      if (["ready", "failed"].includes(data.status)) {
        clearInterval(timer);
        timer = null;
      }
    } catch (error) {
      text(elements.message, error.message);
      elements.message.classList.add("error");
    }
  };
  elements.reveal.addEventListener("click", async () => {
    elements.reveal.disabled = true;
    try {
      const data = await api(`/api/rounds/${encodeURIComponent(roundId)}/reveal`, { method: "POST" });
      text(elements.answer, `${data.key} ${data.scale}`);
      text(elements.confidence, data.low_confidence ? "Análise com baixa confiança — use o resultado como referência." : "Análise com boa confiança.");
      elements.answerPanel.hidden = false;
      elements.reveal.hidden = true;
    } catch (error) {
      text(elements.message, error.message);
      elements.reveal.disabled = false;
    }
  });
  document.querySelectorAll("[data-result]").forEach((button) => button.addEventListener("click", async () => {
    document.querySelectorAll("[data-result]").forEach((item) => { item.disabled = true; });
    try {
      await api(`/api/rounds/${encodeURIComponent(roundId)}/result`, { method: "POST", body: JSON.stringify({ result: button.dataset.result }) });
      text(elements.message, button.dataset.result === "correct" ? "Boa! Acerto registrado." : "Tudo bem — erro registrado para acompanhar sua evolução.");
      elements.nextArea.hidden = false;
    } catch (error) { text(elements.message, error.message); }
  }));
  elements.next.addEventListener("click", async () => {
    elements.next.disabled = true;
    try {
      const data = await api(`/api/playlists/${encodeURIComponent(playlistId)}/rounds`, { method: "POST" });
      window.location.assign(`/game/${encodeURIComponent(data.round_id)}`);
    } catch (error) { text(elements.message, error.message); elements.next.disabled = false; }
  });
  poll();
  timer = setInterval(poll, 1500);
};

const setupStats = async () => {
  const shell = document.querySelector(".stats-shell");
  if (!shell || shell.dataset.initialized) return;
  shell.dataset.initialized = "true";
  const status = document.querySelector("#stats-status");
  try {
    const data = await api("/api/stats");
    text(document.querySelector("#metric-total"), data.total);
    text(document.querySelector("#metric-correct"), data.correct);
    text(document.querySelector("#metric-incorrect"), data.incorrect);
    text(document.querySelector("#metric-accuracy"), `${data.accuracy}%`);
    const rows = document.querySelector("#key-rows");
    data.by_key.forEach((item) => {
      const row = document.createElement("tr");
      [item.key, `${item.correct}/${item.total}`, `${item.accuracy}%`].forEach((value) => {
        const cell = document.createElement("td"); cell.textContent = value; row.appendChild(cell);
      });
      rows.appendChild(row);
    });
    document.querySelector("#keys-empty").hidden = data.by_key.length > 0;
    const history = document.querySelector("#history-list");
    data.recent.forEach((item) => {
      const entry = document.createElement("li");
      const description = document.createElement("span");
      const title = document.createElement("strong"); title.textContent = item.title;
      const detail = document.createElement("small"); detail.textContent = `${item.artist || "Artista não informado"} · ${item.key} ${item.scale}`;
      description.append(title, document.createElement("br"), detail);
      const result = document.createElement("span"); result.className = item.result; result.textContent = item.result === "correct" ? "Acerto" : "Erro";
      entry.append(description, result); history.appendChild(entry);
    });
    document.querySelector("#history-empty").hidden = data.recent.length > 0;
    document.querySelector("#stats-content").hidden = false;
    text(status, "");
  } catch (error) { status.classList.add("error"); text(status, error.message); }
};

const initializePage = () => { setupHome(); setupGame(); setupStats(); };
document.addEventListener("DOMContentLoaded", initializePage);
document.addEventListener("htmx:load", initializePage);
