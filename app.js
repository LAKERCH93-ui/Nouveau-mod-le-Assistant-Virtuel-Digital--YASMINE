const messages = document.querySelector('#messages');
const composer = document.querySelector('#composer');
const input = document.querySelector('#message-input');
const micButton = document.querySelector('#mic-button');
const micLabel = document.querySelector('#mic-label');
const orbStatus = document.querySelector('#orb-status');
const soundToggle = document.querySelector('#sound-toggle');
const lockButton = document.querySelector('#lock-button');
const lockScreen = document.querySelector('#lock-screen');
const unlockForm = document.querySelector('#unlock-form');
const passwordInput = document.querySelector('#password-input');
const passwordConfirm = document.querySelector('#password-confirm');
const confirmWrap = document.querySelector('#confirm-wrap');
const unlockTitle = document.querySelector('#lock-title');
const unlockDescription = document.querySelector('#lock-description');
const unlockSubmit = document.querySelector('#unlock-submit');
const unlockFeedback = document.querySelector('#unlock-feedback');
const dialog = document.querySelector('#info-dialog');

let token = sessionStorage.getItem('yasmine-token') || '';
let voiceEnabled = true;
let recognition;
let listening = false;
let continuousListening = false;
let awaitingReply = false;
let locked = true;
let inactivityTimer;
let trainingLoaded = false;
let agentLoaded = false;

function stamp() {
  return new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit' }).format(new Date());
}

function escapeHtml(value) {
  const node = document.createElement('div');
  node.textContent = String(value);
  return node.innerHTML;
}

function feedback(element, text = '', error = false) {
  element.textContent = text;
  element.classList.toggle('error', error);
  element.classList.toggle('success', Boolean(text) && !error);
}

function addMessage(text, author = 'assistant', typing = false) {
  const message = document.createElement('article');
  message.className = `message ${author === 'assistant' ? 'assistant-message' : 'user-message'}`;
  const initials = author === 'assistant' ? 'Y' : 'V';
  const name = author === 'assistant' ? 'YASMINE' : 'VOUS';
  message.innerHTML = `<span class="message-avatar">${initials}</span><div><p class="message-name">${name} <time>${stamp()}</time></p>${typing ? '<span class="typing"><i></i><i></i><i></i></span>' : `<p>${escapeHtml(text)}</p>`}</div>`;
  messages.append(message);
  messages.scrollTop = messages.scrollHeight;
  return message;
}

function addAction(action) {
  if (!action || action.type !== 'ouvrir_application') return;
  const message = document.createElement('article');
  message.className = 'message assistant-message action-message';
  message.innerHTML = `<span class="message-avatar">Y</span><div><p class="message-name">YASMINE <time>${stamp()}</time></p><div class="action-card"><p>Action locale demandée : <strong>${escapeHtml(action.nom)}</strong></p><button class="primary-action action-confirm" type="button">Ouvrir ${escapeHtml(action.nom)}</button><p class="action-note">Une confirmation est requise. Cette demande expire dans 90 secondes.</p></div></div>`;
  const button = message.querySelector('.action-confirm');
  button.addEventListener('click', async () => {
    button.disabled = true;
    button.textContent = 'Ouverture…';
    try {
      const response = await api('/api/actions/confirmer', { method: 'POST', body: JSON.stringify({ id: action.id }) });
      button.textContent = 'Ouvert';
      addMessage(response.message);
      speak(response.message);
    } catch (error) {
      button.textContent = 'Action non effectuée';
      addMessage(error.message);
    }
  });
  messages.append(message);
  messages.scrollTop = messages.scrollHeight;
}

async function api(path, options = {}, refreshSession = true) {
  const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) };
  if (token) headers['X-Yasmine-Token'] = token;
  try {
    const response = await fetch(path, { ...options, headers });
    const body = await response.json();
    if (response.status === 401 && path !== '/api/security/unlock') {
      verrouiller('La session a expiré après une heure sans interaction.');
      throw new Error('Session verrouillée.');
    }
    if (!response.ok) throw new Error(body.erreur || 'Une erreur est survenue.');
    if (refreshSession && token && !path.includes('/notifications')) touchActivity();
    return body;
  } catch (error) {
    if (error instanceof Error && error.message === 'Session verrouillée.') throw error;
    if (error instanceof TypeError && /fetch/i.test(error.message)) {
      throw new Error('Serveur Yasmine inaccessible. Lancez « py yasmine_server.py », puis ouvrez http://127.0.0.1:8765.');
    }
    throw new Error(error instanceof Error ? error.message : 'Le serveur Yasmine est inaccessible. Lancez yasmine_server.py.');
  }
}

function speak(text) {
  if (!voiceEnabled || !('speechSynthesis' in window)) {
    awaitingReply = false;
    resumeListening();
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'fr-FR';
  utterance.rate = 1.03;
  utterance.pitch = 1.02;
  utterance.onend = () => { awaitingReply = false; resumeListening(); };
  utterance.onerror = () => { awaitingReply = false; resumeListening(); };
  window.speechSynthesis.speak(utterance);
}

function resumeListening() {
  if (continuousListening && recognition && !listening && !locked && !window.speechSynthesis?.speaking) {
    window.setTimeout(() => { if (!listening && !locked && !awaitingReply) recognition.start(); }, 350);
  }
}

function showAlerts(alerts = []) {
  alerts.forEach((alert) => {
    addMessage(alert);
    speak(alert);
  });
}

function touchActivity() {
  if (locked) return;
  window.clearTimeout(inactivityTimer);
  inactivityTimer = window.setTimeout(() => verrouiller('Une heure sans interaction : Yasmine est verrouillée.'), 60 * 60 * 1000);
}

function setGate(configure, message = '') {
  lockScreen.hidden = false;
  document.body.classList.add('locked');
  confirmWrap.hidden = !configure;
  passwordConfirm.required = configure;
  passwordInput.autocomplete = configure ? 'new-password' : 'current-password';
  unlockTitle.textContent = configure ? 'Protégez Yasmine' : 'Activez Yasmine';
  unlockDescription.textContent = configure
    ? 'Créez le mot de passe qui sera demandé après une heure sans interaction.'
    : 'Saisissez votre mot de passe pour reprendre la conversation.';
  unlockSubmit.textContent = configure ? 'Créer et activer' : 'Déverrouiller';
  feedback(unlockFeedback, message, Boolean(message));
  passwordInput.value = '';
  passwordConfirm.value = '';
  window.setTimeout(() => passwordInput.focus(), 50);
}

function verrouiller(message = '') {
  locked = true;
  token = '';
  sessionStorage.removeItem('yasmine-token');
  window.clearTimeout(inactivityTimer);
  if (listening && recognition) recognition.stop();
  setGate(false, message);
}

async function unlock() {
  locked = false;
  lockScreen.hidden = true;
  document.body.classList.remove('locked');
  feedback(unlockFeedback);
  touchActivity();
  try {
    const status = await api('/api/status');
    orbStatus.textContent = status.modele_local ? 'IA LOCALE' : 'EN VEILLE';
    await Promise.all([loadAgent(), loadTraining(), loadSettings()]);
  } catch (error) {
    addMessage(error.message);
  }
}

async function bootstrap() {
  try {
    const status = await api('/api/security/status', {}, false);
    if (!status.mot_de_passe_configure) {
      setGate(true);
      return;
    }
    if (token) {
      try {
        await unlock();
      } catch {
        setGate(false);
      }
    } else setGate(false);
  } catch (error) {
    setGate(false, 'Serveur local introuvable. Lancez « py yasmine_server.py ».');
  }
}

unlockForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const creating = !confirmWrap.hidden;
  const password = passwordInput.value;
  if (creating && password !== passwordConfirm.value) {
    feedback(unlockFeedback, 'Les mots de passe ne correspondent pas.', true);
    return;
  }
  try {
    unlockSubmit.disabled = true;
    feedback(unlockFeedback, creating ? 'Protection en cours…' : 'Vérification…');
    const path = creating ? '/api/security/setup' : '/api/security/unlock';
    const response = await api(path, { method: 'POST', body: JSON.stringify({ mot_de_passe: password }) }, false);
    token = response.jeton;
    sessionStorage.setItem('yasmine-token', token);
    await unlock();
  } catch (error) {
    feedback(unlockFeedback, error.message, true);
  } finally {
    unlockSubmit.disabled = false;
  }
});

async function send(text) {
  if (locked || !text.trim()) return;
  touchActivity();
  awaitingReply = true;
  addMessage(text, 'user');
  input.value = '';
  const typing = addMessage('', 'assistant', true);
  orbStatus.textContent = 'ANALYSE';
  try {
    const body = await api('/api/chat', { method: 'POST', body: JSON.stringify({ message: text }) });
    typing.remove();
    addMessage(body.reponse);
    addAction(body.action);
    speak(body.reponse);
    showAlerts(body.alertes);
  } catch (error) {
    typing.remove();
    addMessage(error.message);
    awaitingReply = false;
    resumeListening();
  } finally {
    if (!locked) orbStatus.textContent = 'EN VEILLE';
  }
}

composer.addEventListener('submit', (event) => { event.preventDefault(); send(input.value); });
document.querySelectorAll('[data-prompt]').forEach((button) => button.addEventListener('click', () => send(button.dataset.prompt)));
document.querySelector('#clear-chat').addEventListener('click', () => {
  touchActivity();
  messages.innerHTML = '';
  addMessage('Historique visuel effacé. Je reste disponible.');
});
soundToggle.addEventListener('click', () => {
  voiceEnabled = !voiceEnabled;
  soundToggle.setAttribute('aria-label', voiceEnabled ? 'Désactiver la voix' : 'Activer la voix');
  soundToggle.style.color = voiceEnabled ? '' : '#617779';
  if (!voiceEnabled) window.speechSynthesis?.cancel();
  touchActivity();
});
lockButton.addEventListener('click', () => verrouiller('Yasmine a été verrouillée.'));
document.querySelector('#privacy-button').addEventListener('click', () => dialog.showModal());
document.querySelector('.dialog-close').addEventListener('click', () => dialog.close());

document.querySelectorAll('.deck-tab').forEach((tab) => tab.addEventListener('click', () => {
  touchActivity();
  document.querySelectorAll('.deck-tab').forEach((button) => {
    const current = button === tab;
    button.classList.toggle('active', current);
    button.setAttribute('aria-selected', String(current));
  });
  document.querySelectorAll('.deck-panel').forEach((panel) => {
    const current = panel.id === tab.dataset.tab;
    panel.hidden = !current;
    panel.classList.toggle('active', current);
  });
  if (tab.dataset.tab === 'agent-panel' && !agentLoaded) loadAgent();
  if (tab.dataset.tab === 'training-panel' && !trainingLoaded) loadTraining();
}));

function renderAgent(agent) {
  const memory = agent.memoire || {};
  const skills = agent.competences || [];
  const activeSkills = skills.filter((skill) => skill.actif).length;
  document.querySelector('#agent-identity').textContent = `${agent.nom || 'Yasmine'} / AGENT`;
  document.querySelector('#agent-brain').textContent = agent.cerveau?.etat || 'Indisponible';
  document.querySelector('#agent-model').textContent = agent.cerveau?.modele || '—';
  document.querySelector('#agent-memory').textContent = `${memory.notes || 0} NOTES · ${memory.rappels || 0} RAPPELS`;
  document.querySelector('#agent-profile').textContent = memory.prenom ? `Profil : ${memory.prenom}` : 'Profil local non renseigné';
  document.querySelector('#agent-skills-total').textContent = `${activeSkills} / ${skills.length}`;

  const skillsList = document.querySelector('#skills-list');
  skillsList.innerHTML = skills.map((skill) => `<article class="skill-card ${skill.actif ? 'enabled' : 'disabled'}"><div><h3>${escapeHtml(skill.nom)}</h3><p>${escapeHtml(skill.description)}</p></div><button class="skill-toggle" type="button" data-skill="${escapeHtml(skill.id)}" aria-pressed="${String(skill.actif)}">${skill.actif ? 'ACTIF' : 'PAUSE'}</button></article>`).join('');
  skillsList.querySelectorAll('[data-skill]').forEach((button) => button.addEventListener('click', async () => {
    const id = button.dataset.skill;
    const current = skills.find((skill) => skill.id === id);
    if (!current) return;
    button.disabled = true;
    try {
      const updated = await api('/api/skills', { method: 'POST', body: JSON.stringify({ id, actif: !current.actif }) });
      renderAgent(updated);
    } catch (error) {
      addMessage(error.message);
      button.disabled = false;
    }
  }));

  const connections = document.querySelector('#connections-list');
  connections.innerHTML = (agent.connecteurs || []).map((connector) => `<article class="connection-card"><div><h3>${escapeHtml(connector.nom)}</h3><p>${escapeHtml(connector.detail)}</p></div><div><span>${escapeHtml(connector.service)}</span><small>${escapeHtml(connector.etat)}</small></div></article>`).join('');
  agentLoaded = true;
}

async function loadAgent() {
  try {
    const body = await api('/api/agent');
    renderAgent(body);
  } catch (error) {
    document.querySelector('#skills-list').innerHTML = `<p class="form-feedback error">${escapeHtml(error.message)}</p>`;
  }
}

function renderTraining(intentions) {
  const container = document.querySelector('#intentions-list');
  const keys = Object.keys(intentions).sort();
  const phrases = keys.reduce((sum, key) => sum + (intentions[key].exemples || []).length, 0);
  document.querySelector('#training-count').textContent = `${phrases} phrases · ${keys.length} intentions`;
  container.innerHTML = keys.map((name) => {
    const entry = intentions[name] || {};
    const examples = (entry.exemples || []).map((item) => `<li>${escapeHtml(item)}</li>`).join('');
    const answers = (entry.reponses || []).map((item) => `<li>${escapeHtml(item)}</li>`).join('');
    return `<details class="intent-card"><summary><span>${escapeHtml(name)}</span><span class="intent-badge">${(entry.exemples || []).length} PHRASE(S)</span></summary><div class="intent-content"><h4>PHRASES D’ENTRAÎNEMENT</h4><ul>${examples}</ul><h4>RÉPONSES POSSIBLES</h4><ul>${answers}</ul></div></details>`;
  }).join('');
  trainingLoaded = true;
}

async function loadTraining() {
  try {
    const body = await api('/api/training');
    renderTraining(body.intentions);
  } catch (error) {
    document.querySelector('#intentions-list').innerHTML = `<p class="form-feedback error">${escapeHtml(error.message)}</p>`;
  }
}

document.querySelector('#train-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  touchActivity();
  const form = event.currentTarget;
  const result = document.querySelector('#training-feedback');
  const payload = {
    intention: document.querySelector('#intent-input').value,
    exemple: document.querySelector('#example-input').value,
    reponse: document.querySelector('#answer-input').value,
  };
  try {
    feedback(result, 'Réentraînement en cours…');
    const body = await api('/api/training', { method: 'POST', body: JSON.stringify(payload) });
    feedback(result, `${body.message} ${body.phrases} phrases, ${body.intentions} intentions.`);
    form.reset();
    trainingLoaded = false;
    await loadTraining();
  } catch (error) {
    feedback(result, error.message, true);
  }
});

async function loadSettings() {
  try {
    const settings = await api('/api/settings');
    document.querySelector('#llm-enabled').checked = settings.llm_active;
    document.querySelector('#ollama-url').value = settings.ollama_url;
    document.querySelector('#ollama-model').value = settings.ollama_modele;
  } catch (error) {
    feedback(document.querySelector('#settings-feedback'), error.message, true);
  }
}

document.querySelector('#settings-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  touchActivity();
  const result = document.querySelector('#settings-feedback');
  try {
    const body = await api('/api/settings', {
      method: 'POST',
      body: JSON.stringify({
        llm_active: document.querySelector('#llm-enabled').checked,
        ollama_url: document.querySelector('#ollama-url').value,
        ollama_modele: document.querySelector('#ollama-model').value,
      }),
    });
    feedback(result, body.llm_active ? 'Modèle local activé.' : 'Mode modèle local désactivé.');
    orbStatus.textContent = body.llm_active ? 'IA LOCALE' : 'EN VEILLE';
  } catch (error) {
    feedback(result, error.message, true);
  }
});

document.querySelector('#continuous-listening').addEventListener('change', (event) => {
  continuousListening = event.target.checked;
  if (!continuousListening && listening && recognition) recognition.stop();
  touchActivity();
});

async function checkNotifications() {
  if (locked || !token) return;
  try {
    const body = await api('/api/notifications', {}, false);
    showAlerts(body.alertes);
  } catch (error) {
    if (error.message !== 'Session verrouillée.') console.debug(error.message);
  }
}

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
if (SpeechRecognition) {
  recognition = new SpeechRecognition();
  recognition.lang = 'fr-FR';
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.onstart = () => {
    listening = true;
    micButton.classList.add('listening');
    micLabel.textContent = 'ÉCOUTE…';
    orbStatus.textContent = 'ÉCOUTE';
  };
  recognition.onresult = (event) => {
    let finalText = '';
    let interimText = '';
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      if (event.results[index].isFinal) finalText += event.results[index][0].transcript;
      else interimText += event.results[index][0].transcript;
    }
    input.value = finalText || interimText;
    if (finalText) {
      awaitingReply = true;
      if (listening) recognition.stop();
      send(finalText);
    }
  };
  recognition.onerror = (event) => {
    if (event.error !== 'aborted' && event.error !== 'no-speech') addMessage('Le microphone n’est pas disponible. Vérifiez l’autorisation du navigateur, ou écrivez votre message.');
  };
  recognition.onend = () => {
    listening = false;
    micButton.classList.remove('listening');
    micLabel.textContent = 'PARLER';
    if (!locked && orbStatus.textContent === 'ÉCOUTE') orbStatus.textContent = 'EN VEILLE';
    if (!awaitingReply) resumeListening();
  };
  micButton.addEventListener('click', () => {
    if (locked) return;
    touchActivity();
    if (listening) recognition.stop();
    else {
      recognition.continuous = continuousListening;
      recognition.start();
    }
  });
} else {
  micButton.disabled = true;
  micLabel.textContent = 'NON PRIS EN CHARGE';
  micButton.title = 'La reconnaissance vocale n’est pas disponible dans ce navigateur.';
}

window.setInterval(checkNotifications, 10_000);
bootstrap();
