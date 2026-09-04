"use strict";

const cookieValue = (name) => {
  const found = document.cookie.split("; ").find((entry) => entry.startsWith(`${name}=`));
  return found ? decodeURIComponent(found.split("=").slice(1).join("=")) : "";
};

const api = async (url, options = {}) => {
  const headers = new Headers(options.headers || {});
  if (options.method && options.method !== "GET") {
    headers.set("X-CSRF-Token", cookieValue("key_hunt_csrf"));
  }
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
      const data = await api("/api/playlists/import", {
        method: "POST", body: JSON.stringify({ url: input.value }),
      });
      window.location.assign(`/game/session/${encodeURIComponent(data.session_id)}`);
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
  audio_ready: ["Áudio pronto", "Você já pode ouvir. Analisando o tom…"],
  analyzing: ["Analisando", "Você já pode ouvir enquanto a análise termina…"],
  ready: ["Pronto", "Ouça, decida e revele quando quiser."],
  failed: ["Falha", "Não foi possível preparar esta faixa."],
};

const setupGame = () => {
  const shell = document.querySelector(".game-shell");
  if (!shell || shell.dataset.initialized) return;
  shell.dataset.initialized = "true";
  let roundId = shell.dataset.roundId;
  const sessionId = shell.dataset.sessionId;
  let playlistId = "";
  let timer = null;
  let advancing = false;
  const elements = {
    stage: document.querySelector("#music-stage"), badge: document.querySelector("#status-badge"),
    processing: document.querySelector("#processing-text"), position: document.querySelector("#position-badge"),
    prefetch: document.querySelector("#prefetch-badge"), title: document.querySelector("#track-title"),
    artist: document.querySelector("#track-artist"), ambient: document.querySelector("#ambient-cover"),
    thumbnail: document.querySelector("#thumbnail"), playerWrap: document.querySelector("#player-wrap"),
    player: document.querySelector("#audio-player"), playerToggle: document.querySelector("#player-toggle"),
    replay: document.querySelector("#replay-button"), progress: document.querySelector("#player-progress"),
    currentTime: document.querySelector("#current-time"), durationTime: document.querySelector("#duration-time"),
    playerVolume: document.querySelector("#player-volume"), mute: document.querySelector("#mute-button"),
    reveal: document.querySelector("#reveal-button"), message: document.querySelector("#game-message"),
    answerPanel: document.querySelector("#answer-panel"), answer: document.querySelector("#answer"),
    confidence: document.querySelector("#confidence-note"), nextArea: document.querySelector("#next-area"),
    next: document.querySelector("#next-button"), finished: document.querySelector("#finished-area"),
    reshuffle: document.querySelector("#reshuffle-button"),
  };
  const formatTime = (seconds) => {
    if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
    return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  };
  const playerState = (state) => {
    elements.playerWrap.dataset.state = state;
    text(elements.playerToggle, state === "playing" ? "❚❚" : "▶");
    elements.playerToggle.setAttribute(
      "aria-label", state === "playing" ? "Pausar música" : "Reproduzir música",
    );
  };
  elements.playerToggle?.addEventListener("click", () => {
    if (!elements.player?.src) return;
    if (elements.player.paused) elements.player.play().catch(() => playerState("error"));
    else elements.player.pause();
  });
  elements.replay?.addEventListener("click", () => {
    elements.player.currentTime = Math.max(0, elements.player.currentTime - 5);
  });
  elements.progress?.addEventListener("input", (event) => {
    if (Number.isFinite(elements.player.duration)) {
      elements.player.currentTime = (Number(event.target.value) / 1000) * elements.player.duration;
    }
  });
  elements.playerVolume?.addEventListener("input", (event) => {
    elements.player.volume = Number(event.target.value);
    elements.player.muted = false;
  });
  elements.mute?.addEventListener("click", () => {
    elements.player.muted = !elements.player.muted;
    text(elements.mute, elements.player.muted ? "🔇" : "🔊");
    elements.mute.setAttribute("aria-label", elements.player.muted ? "Ativar som" : "Silenciar");
  });
  elements.player?.addEventListener("loadedmetadata", () => {
    text(elements.durationTime, formatTime(elements.player.duration));
    elements.player.volume = Number(elements.playerVolume?.value || 0.8);
    playerState("paused");
  });
  elements.player?.addEventListener("timeupdate", () => {
    text(elements.currentTime, formatTime(elements.player.currentTime));
    const ratio = elements.player.duration ? elements.player.currentTime / elements.player.duration : 0;
    elements.progress.value = String(Math.round(ratio * 1000));
    elements.progress.style.setProperty("--progress", `${ratio * 100}%`);
  });
  elements.player?.addEventListener("play", () => playerState("playing"));
  elements.player?.addEventListener("pause", () => playerState("paused"));
  elements.player?.addEventListener("waiting", () => playerState("buffering"));
  elements.player?.addEventListener("playing", () => playerState("playing"));
  elements.player?.addEventListener("ended", () => playerState("ended"));
  elements.player?.addEventListener("error", () => playerState("error"));

  const resetRound = (newRoundId) => {
    roundId = newRoundId;
    elements.player.pause();
    elements.player.removeAttribute("src");
    elements.player.load();
    elements.playerWrap.hidden = true;
    elements.answerPanel.hidden = true;
    elements.nextArea.hidden = true;
    elements.next.disabled = false;
    elements.reveal.hidden = false;
    elements.reveal.disabled = true;
    text(elements.reveal, "Analisando tom…");
    text(elements.answer, "");
    text(elements.confidence, "");
    text(elements.message, "");
    document.querySelectorAll("[data-result]").forEach((item) => { item.disabled = false; });
    elements.stage.classList.add("is-changing");
    window.setTimeout(() => elements.stage.classList.remove("is-changing"), 260);
  };
  const applyRound = (data, position, total) => {
    playlistId = data.playlist_id;
    text(elements.title, data.track.title);
    text(elements.artist, data.track.artist || "Artista não informado");
    elements.title.classList.remove("skeleton-text");
    elements.stage.classList.remove("skeleton");
    if (Number.isInteger(position) && Number.isInteger(total)) {
      text(elements.position, `Faixa ${position + 1} de ${total}`);
      elements.position.hidden = false;
    }
    if (data.track.thumbnail_url) {
      elements.thumbnail.src = data.track.thumbnail_url;
      elements.thumbnail.alt = `Capa de ${data.track.title}`;
      elements.thumbnail.hidden = false;
      elements.ambient.style.backgroundImage = `url("${data.track.thumbnail_url.replaceAll('"', '%22')}")`;
      elements.thumbnail.onerror = () => { elements.thumbnail.hidden = true; };
    } else {
      elements.thumbnail.hidden = true;
      elements.ambient.style.backgroundImage = "none";
    }
    const label = labels[data.status] || labels.queued;
    text(elements.badge, label[0]);
    text(elements.processing, data.error || label[1]);
    if (data.audio_url && !elements.player.getAttribute("src")) {
      elements.player.src = data.audio_url;
      elements.playerWrap.hidden = false;
      playerState("loading");
    }
    elements.reveal.disabled = !data.can_reveal;
    text(elements.reveal, data.can_reveal ? "Revelar tom" : "Analisando tom…");
    if (data.answered) {
      elements.reveal.hidden = true;
      elements.nextArea.hidden = false;
      elements.next.disabled = false;
      text(elements.message, "Resposta já registrada. Você pode seguir para a próxima música.");
    }
    return ["ready", "failed"].includes(data.status);
  };
  const updatePrefetch = async () => {
    if (!sessionId) return;
    try {
      const status = await api(`/api/sessions/${encodeURIComponent(sessionId)}/prefetch-status`);
      text(elements.prefetch, status.next_ready ? "Próxima faixa pronta" : "Preparando próximas faixas");
      elements.prefetch.hidden = status.requested_ahead === 0;
    } catch (_) { elements.prefetch.hidden = true; }
  };
  const poll = async () => {
    try {
      if (sessionId) {
        const state = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
        if (state.finished || !state.round) {
          elements.finished.hidden = false;
          if (timer) clearInterval(timer);
          timer = null;
          return;
        }
        roundId = state.round.round_id;
        if (applyRound(state.round, state.position, state.total_tracks) && timer) {
          clearInterval(timer); timer = null;
        }
        await updatePrefetch();
      } else {
        const data = await api(`/api/rounds/${encodeURIComponent(roundId)}/status`);
        if (applyRound(data, null, null) && timer) { clearInterval(timer); timer = null; }
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
      text(elements.confidence, data.low_confidence ? "Baixa confiança — use como referência." : "Análise com boa confiança.");
      elements.answerPanel.hidden = false;
      elements.reveal.hidden = true;
    } catch (error) { text(elements.message, error.message); elements.reveal.disabled = false; }
  });
  document.querySelectorAll("[data-result]").forEach((button) => button.addEventListener("click", async () => {
    document.querySelectorAll("[data-result]").forEach((item) => { item.disabled = true; });
    try {
      await api(`/api/rounds/${encodeURIComponent(roundId)}/result`, {
        method: "POST", body: JSON.stringify({ result: button.dataset.result }),
      });
      text(elements.message, button.dataset.result === "correct" ? "Boa! Acerto registrado." : "Erro registrado — seguimos treinando.");
      elements.next.disabled = false;
      elements.nextArea.hidden = false;
    } catch (error) { text(elements.message, error.message); }
  }));
  elements.next.addEventListener("click", async () => {
    if (advancing) return;
    advancing = true;
    elements.next.disabled = true;
    try {
      if (sessionId) {
        const state = await api(`/api/sessions/${encodeURIComponent(sessionId)}/next`, {
          method: "POST", body: JSON.stringify({ idempotency_key: `next-${roundId}` }),
        });
        if (state.finished || !state.round) {
          elements.nextArea.hidden = true;
          elements.finished.hidden = false;
          return;
        }
        resetRound(state.round.round_id);
        applyRound(state.round, state.position, state.total_tracks);
      } else {
        const data = await api(`/api/playlists/${encodeURIComponent(playlistId)}/rounds`, { method: "POST" });
        window.location.assign(`/game/${encodeURIComponent(data.round_id)}`);
        return;
      }
      timer = window.setInterval(poll, 1500);
      await updatePrefetch();
    } catch (error) {
      text(elements.message, error.message);
      elements.next.disabled = false;
    } finally { advancing = false; }
  });
  elements.reshuffle?.addEventListener("click", async () => {
    elements.reshuffle.disabled = true;
    try {
      const data = await api(`/api/playlists/${encodeURIComponent(playlistId)}/sessions`, { method: "POST" });
      window.location.assign(`/game/session/${encodeURIComponent(data.session_id)}`);
    } catch (error) { text(elements.message, error.message); elements.reshuffle.disabled = false; }
  });
  poll();
  timer = window.setInterval(poll, 1500);
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
      const detail = document.createElement("small");
      detail.textContent = `${item.artist || "Artista não informado"} · ${item.key} ${item.scale}`;
      description.append(title, document.createElement("br"), detail);
      const result = document.createElement("span"); result.className = item.result;
      result.textContent = item.result === "correct" ? "Acerto" : "Erro";
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
