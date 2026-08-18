/* ai-hawk - interface de chat */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = {
    model: $("model"),
    modelMeta: $("model-meta"),
    system: $("system"),
    apikey: $("apikey"),
    temperature: $("temperature"),
    tempValue: $("temp-value"),
    stream: $("stream"),
    newChat: $("new-chat"),
    reload: $("reload-models"),
    messages: $("messages"),
    composer: $("composer"),
    input: $("input"),
    send: $("send"),
    dot: $("status-dot"),
    statusText: $("status-text"),
  };

  // Base da API derivada da URL da propria pagina. Faz a interface funcionar
  // tanto na raiz (http://host:8081/) quanto atras de um proxy reverso com
  // prefixo (https://dominio/ai-hawk/). Sempre termina em "/".
  const API_BASE = new URL(".", document.baseURI).href;

  const STORE = "ai-hawk.settings";
  const settings = loadSettings();
  let models = [];
  let history = [];
  let busy = false;
  let aborter = null;
  let descartarParcial = false;

  // ------------------------------------------------------------------ store

  function loadSettings() {
    try {
      return JSON.parse(localStorage.getItem(STORE)) || {};
    } catch {
      return {};
    }
  }

  function saveSettings() {
    settings.apikey = el.apikey.value.trim();
    // So grava o modelo quando ha um de verdade: mexer na temperatura antes de
    // a lista chegar apagava a escolha guardada da conversa anterior.
    if (el.model.value) settings.model = el.model.value;
    settings.system = el.system.value;
    settings.temperature = el.temperature.value;
    settings.stream = el.stream.checked;
    localStorage.setItem(STORE, JSON.stringify(settings));
  }

  function applySettings() {
    el.apikey.value = settings.apikey || "";
    el.system.value = settings.system || "";
    el.temperature.value = settings.temperature ?? "0.7";
    el.tempValue.textContent = el.temperature.value;
    el.stream.checked = settings.stream !== false;
  }

  // ------------------------------------------------------------------- http

  function headers() {
    const h = { "Content-Type": "application/json" };
    const key = el.apikey.value.trim();
    if (key) h.Authorization = `Bearer ${key}`;
    return h;
  }

  /** O botao de enviar vira o de parar enquanto a resposta esta chegando. */
  function marcarOcupado(estado) {
    busy = estado;
    el.send.classList.toggle("parar", estado);
    el.send.setAttribute("aria-label", estado ? "Parar" : "Enviar");
    el.send.title = estado ? "Parar a resposta" : "Enviar";
  }

  /**
   * Interrompe a resposta em andamento.
   * @param descartar - true joga fora o pedaco ja recebido (nova conversa);
   *                    false guarda o que chegou (o usuario mandou parar).
   */
  function pararResposta(descartar) {
    if (!aborter) return;
    descartarParcial = descartar;
    aborter.abort();
  }

  function setStatus(text, state) {
    el.statusText.textContent = text;
    el.dot.className = `dot ${state || ""}`.trim();
  }

  /** Abre os Ajustes e coloca o cursor no campo da chave. */
  function pedirChave(mensagem) {
    setStatus(mensagem, "err");
    const ajustes = el.apikey.closest("details");
    if (ajustes) ajustes.open = true;
    el.apikey.focus();
    el.apikey.classList.add("destaque");
  }

  async function loadModels(refresh) {
    setStatus("carregando modelos...", "");
    try {
      const url = `${API_BASE}v1/models${refresh ? "?refresh=true" : ""}`;
      const res = await fetch(url, { headers: headers() });

      // 401 tem tratamento proprio: o corpo pode ser HTML do proxy, nao JSON,
      // e a acao que resolve e sempre a mesma - informar a chave.
      if (res.status === 401) {
        models = [];
        renderModelOptions();
        pedirChave(
          el.apikey.value.trim()
            ? "Chave invalida - confira em Ajustes"
            : "Informe a chave da API em Ajustes"
        );
        return;
      }

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error?.message || `Falha HTTP ${res.status}`);
      }

      models = (await res.json()).data || [];
      renderModelOptions();
      el.apikey.classList.remove("destaque");

      if (models.length === 0) {
        setStatus("nenhum modelo disponivel no servidor", "err");
      } else {
        const free = models.filter((m) => m.tier === "free").length;
        setStatus(`${models.length} modelos (${free} gratuitos)`, "ok");
      }
    } catch (err) {
      models = [];
      renderModelOptions();
      setStatus(
        err instanceof TypeError ? "servidor inacessivel" : err.message,
        "err"
      );
    }
  }

  function renderModelOptions() {
    el.model.innerHTML = "";
    if (models.length === 0) {
      const opt = document.createElement("option");
      opt.textContent = "-- nenhum modelo --";
      opt.value = "";
      el.model.appendChild(opt);
      updateModelMeta();
      return;
    }

    const groups = new Map();
    for (const m of models) {
      if (!groups.has(m.provider)) groups.set(m.provider, []);
      groups.get(m.provider).push(m);
    }

    for (const [provider, items] of groups) {
      const group = document.createElement("optgroup");
      group.label = provider;
      for (const m of items) {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = `${m.label}${m.tier === "paid" ? "  [pago]" : ""}`;
        group.appendChild(opt);
      }
      el.model.appendChild(group);
    }

    if (settings.model && models.some((m) => m.id === settings.model)) {
      el.model.value = settings.model;
    }
    updateModelMeta();
  }

  function updateModelMeta() {
    const current = models.find((m) => m.id === el.model.value);
    if (!current) {
      el.modelMeta.textContent = "";
      return;
    }
    const tier = current.tier === "free" ? "gratuito" : "PAGO";
    const ctx = current.context_window
      ? ` - contexto ${Intl.NumberFormat("pt-BR").format(current.context_window)} tokens`
      : "";
    el.modelMeta.textContent = `${current.id} - ${tier}${ctx}`;
  }

  // ---------------------------------------------------------------- render

  function clearEmptyState() {
    const empty = el.messages.querySelector(".empty-state");
    if (empty) empty.remove();
  }

  function addMessage(role, text) {
    clearEmptyState();
    const wrap = document.createElement("div");
    wrap.className = `msg ${role}`;

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = role === "user" ? "EU" : role === "error" ? "!" : "AI";

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    const roleLabel = document.createElement("div");
    roleLabel.className = "bubble-role";
    roleLabel.textContent =
      role === "user" ? "Voce" : role === "error" ? "Erro" : currentModelLabel();

    const body = document.createElement("div");
    body.className = "bubble-body";
    body.innerHTML = renderMarkdown(text);

    bubble.append(roleLabel, body);
    wrap.append(avatar, bubble);
    el.messages.appendChild(wrap);
    scrollToBottom();
    return body;
  }

  function currentModelLabel() {
    const m = models.find((x) => x.id === el.model.value);
    return m ? m.label : "assistente";
  }

  function scrollToBottom() {
    el.messages.scrollTop = el.messages.scrollHeight;
  }

  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** Markdown minimo e seguro: blocos de codigo, codigo inline, negrito. */
  function renderMarkdown(text) {
    const blocks = [];
    let out = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      const cls = lang ? ` class="language-${escapeHtml(lang)}"` : "";
      blocks.push(`<pre><code${cls}>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`);
      return `\u0000BLOCK${blocks.length - 1}\u0000`;
    });

    out = escapeHtml(out)
      .replace(/`([^`\n]+)`/g, (_, c) => `<code>${c}</code>`)
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");

    return out.replace(/\u0000BLOCK(\d+)\u0000/g, (_, i) => blocks[Number(i)]);
  }

  // ------------------------------------------------------------------ envio

  function buildPayload(userText) {
    const messages = [];
    const system = el.system.value.trim();
    if (system) messages.push({ role: "system", content: system });
    messages.push(...history, { role: "user", content: userText });

    return {
      model: el.model.value,
      messages,
      stream: el.stream.checked,
      temperature: Number(el.temperature.value),
    };
  }

  async function send(userText) {
    if (busy) return;
    if (!el.model.value) {
      addMessage("error", "Selecione um modelo antes de enviar.");
      return;
    }

    marcarOcupado(true);
    aborter = new AbortController();
    addMessage("user", userText);

    const payload = buildPayload(userText);
    const target = addMessage("assistant", "");
    target.classList.add("cursor");
    let answer = "";

    try {
      const res = await fetch(`${API_BASE}v1/chat/completions`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify(payload),
        signal: aborter.signal,
      });

      if (res.status === 401) {
        pedirChave("Chave da API necessaria - informe em Ajustes");
        throw new Error(
          "Chave da API necessaria. Abra Ajustes na barra lateral e informe a chave."
        );
      }

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error?.message || `Falha HTTP ${res.status}`);
      }

      if (payload.stream) {
        answer = await consumeStream(res, target, () => answer, (v) => (answer = v));
      } else {
        const data = await res.json();
        answer = data.choices?.[0]?.message?.content || "";
        target.innerHTML = renderMarkdown(answer);
      }

      history.push({ role: "user", content: userText });
      history.push({ role: "assistant", content: answer });
    } catch (err) {
      // Parada a pedido nao e falha: nada de balao de erro. E o pedaco que ja
      // chegou vale a pena guardar, a menos que a conversa toda tenha ido embora.
      if (err.name === "AbortError") {
        if (answer && !descartarParcial) {
          const nota = document.createElement("div");
          nota.className = "aviso";
          nota.textContent = "resposta interrompida";
          target.append(nota);
          history.push({ role: "user", content: userText });
          history.push({ role: "assistant", content: answer });
        } else {
          target.closest(".msg")?.remove();
        }
      } else {
        target.closest(".msg")?.remove();
        addMessage("error", err.message);
      }
    } finally {
      aborter = null;
      descartarParcial = false;
      target.classList.remove("cursor");
      marcarOcupado(false);
      el.input.focus();
      scrollToBottom();
    }
  }

  async function consumeStream(res, target, getAnswer, setAnswer) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";

      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        const raw = line.slice(5).trim();
        if (raw === "[DONE]") continue;

        let chunk;
        try {
          chunk = JSON.parse(raw);
        } catch {
          continue;
        }

        if (chunk.error) throw new Error(chunk.error.message || "erro no provedor");

        const delta = chunk.choices?.[0]?.delta?.content;
        if (delta) {
          setAnswer(getAnswer() + delta);
          target.innerHTML = renderMarkdown(getAnswer());
          scrollToBottom();
        }
      }
    }
    return getAnswer();
  }

  // ---------------------------------------------------------------- eventos

  el.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    // Com a resposta correndo o botao e "parar" - o Enter nao chega aqui.
    if (busy) {
      pararResposta(false);
      return;
    }
    const text = el.input.value.trim();
    if (!text) return;
    el.input.value = "";
    autoResize();
    send(text);
  });

  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      // Durante a resposta o Enter nao envia nem para: so nao apaga o texto.
      if (busy) return;
      el.composer.requestSubmit();
    }
  });

  function autoResize() {
    el.input.style.height = "auto";
    el.input.style.height = `${Math.min(el.input.scrollHeight, 220)}px`;
  }
  el.input.addEventListener("input", autoResize);

  el.temperature.addEventListener("input", () => {
    el.tempValue.textContent = el.temperature.value;
    saveSettings();
  });

  el.model.addEventListener("change", () => {
    updateModelMeta();
    saveSettings();
  });

  [el.apikey, el.system].forEach((node) =>
    node.addEventListener("change", () => {
      saveSettings();
      if (node === el.apikey) loadModels(false);
    })
  );

  el.stream.addEventListener("change", saveSettings);

  el.newChat.addEventListener("click", () => {
    pararResposta(true);
    history = [];
    el.messages.innerHTML =
      '<div class="empty-state"><h2>Como posso ajudar?</h2>' +
      "<p>Escolha um modelo na barra lateral e comece a conversar.</p></div>";
    el.input.focus();
  });

  el.reload.addEventListener("click", () => loadModels(true));

  // ------------------------------------------------------------------ init

  applySettings();
  autoResize();
  loadModels(false);
  el.input.focus();
})();
