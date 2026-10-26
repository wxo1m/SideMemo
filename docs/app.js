/* SideMemo 웹 클라이언트
 * 데스크톱 앱(sidememo.py)과 같은 Supabase 테이블을 그대로 사용합니다.
 *   sidememo_notes    : user_id, note_id, position, payload(jsonb), deleted, updated_at
 *   sidememo_settings : user_id, payload(jsonb), updated_at
 * payload 안에는 데스크톱 앱의 메모 딕셔너리 형태가 그대로 들어 있습니다.
 */
(function () {
  "use strict";

  var FILE_CFG = window.SIDEMEMO_CONFIG || {};
  var CFG = storedConfig() || FILE_CFG;
  var NOTES_TABLE = "sidememo_notes";
  var SETTINGS_TABLE = "sidememo_settings";
  var PUSH_DELAY = 800;
  var THEMES = {
    "노랑": ["#fff2b6", "#d9bd45"],
    "분홍": ["#fad6e6", "#d989ad"],
    "파랑": ["#dce9fb", "#91afd8"],
    "초록": ["#dcf3df", "#94c99f"],
    "보라": ["#eadcf8", "#ae8ad4"]
  };

  function $(id) { return document.getElementById(id); }
  var ui = {
    ver: $("ver"), account: $("account"), rail: $("rail"), editor: $("editor"),
    title: $("title"), attach: $("attach"), status: $("status"),
    syncBtn: $("sync-btn"), themeBtn: $("theme-btn"),
    loginBtn: $("login-btn"), logoutBtn: $("logout-btn"),
    addBtn: $("add-btn"), delBtn: $("del-btn"),
    dlg: $("login-dialog"), email: $("email"), password: $("password"),
    loginErr: $("login-error"), signinBtn: $("signin-btn"),
    signupBtn: $("signup-btn"), closeDlg: $("close-dialog"),
    fontSize: $("font-size"), checkBtn: $("check-btn"), toolbar: $("toolbar"),
    connBtn: $("conn-btn"), connDlg: $("conn-dialog"), connUrl: $("cfg-url"),
    connKey: $("cfg-key"), connErr: $("cfg-error"), connSave: $("cfg-save"),
    connClose: $("cfg-close")
  };

  /* 연결 설정은 이 브라우저에만 저장합니다. */
  function storedConfig() {
    try {
      var raw = localStorage.getItem("sidememo-config");
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      if (parsed && parsed.supabaseUrl && parsed.supabaseAnonKey) return parsed;
    } catch (e) {}
    return null;
  }

  function saveConfig(url, key) {
    try { localStorage.setItem("sidememo-config", JSON.stringify({ supabaseUrl: url, supabaseAnonKey: key })); }
    catch (e) {}
  }

  var db = null;
  var user = null;
  var notes = [];
  var active = -1;
  var tombstones = {};   /* note_id -> true : 삭제 표시 */
  var settings = {};
  var pushTimer = null;
  var channel = null;
  var busy = false;

  /* ------------------------------------------------------------- 공통 유틸 */
  function setStatus(msg, isError) {
    ui.status.textContent = msg;
    ui.status.classList.toggle("error", !!isError);
  }

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "");
    return "n" + Date.now().toString(16) + Math.random().toString(16).slice(2, 10);
  }

  function themeColors(note) {
    if (note && note.theme === "커스텀" && Array.isArray(note.custom_color) && note.custom_color.length === 2) {
      return [String(note.custom_color[0]), String(note.custom_color[1])];
    }
    var name = (note && note.theme) || "노랑";
    return THEMES[name] || THEMES["노랑"];
  }

  function clean(html) {
    return String(html || "")
      .replace(/<script[\s\S]*?<\/script>/gi, "")
      .replace(/<\/?script[^>]*>/gi, "")
      .replace(/\son[a-z]+\s*=\s*"[^"]*"/gi, "")
      .replace(/\son[a-z]+\s*=\s*'[^']*'/gi, "")
      .replace(/javascript:/gi, "");
  }

  function now() {
    var d = new Date();
    function p(v) { return String(v).padStart(2, "0"); }
    return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  /* --------------------------------------------------------------- 상태 변환 */
  function blankNote(index) {
    return {
      id: uuid(),
      title: "NOTE " + (index + 1),
      theme: "노랑",
      font: settings.font || "맑은 고딕",
      font_size: 16,
      tab_slot: (index + 1) + "번 칸",
      html: "",
      attachment: "",
      synced_at: null
    };
  }

  function noteFromRow(row) {
    var payload = {};
    var src = row.payload || {};
    for (var key in src) { if (Object.prototype.hasOwnProperty.call(src, key)) payload[key] = src[key]; }
    payload.id = row.note_id;
    payload.synced_at = row.updated_at || null;
    if (!payload.title) payload.title = "NOTE";
    if (!payload.theme) payload.theme = "노랑";
    if (!payload.font) payload.font = settings.font || "맑은 고딕";
    if (!payload.font_size) payload.font_size = 16;
    return payload;
  }

  function rowFromNote(note, position) {
    var payload = {};
    for (var key in note) {
      if (!Object.prototype.hasOwnProperty.call(note, key)) continue;
      if (key === "synced_at") continue;
      payload[key] = note[key];
    }
    payload.id = note.id;
    return {
      user_id: user.id,
      note_id: note.id,
      position: position,
      payload: payload,
      deleted: false
    };
  }

  function findNote(noteId) {
    for (var i = 0; i < notes.length; i++) if (notes[i].id === noteId) return notes[i];
    return null;
  }

  /* ------------------------------------------------------------------ 렌더링 */
  function render() { renderRail(); renderEditor(); }

  function renderRail() {
    ui.rail.innerHTML = "";
    if (!notes.length) {
      var empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "메모가 없습니다";
      ui.rail.appendChild(empty);
      return;
    }
    notes.forEach(function (note, index) {
      var colors = themeColors(note);
      var tab = document.createElement("button");
      tab.className = "tab" + (index === active ? " active" : "");
      tab.style.background = colors[0];
      tab.style.borderColor = colors[1];
      tab.textContent = note.title || "NOTE";
      tab.addEventListener("click", function () { selectNote(index); });
      ui.rail.appendChild(tab);
    });
  }

  function renderEditor() {
    var note = notes[active];
    var locked = !user || !note;
    ui.title.value = note ? (note.title || "") : "";
    ui.editor.innerHTML = note ? clean(note.html) : "";
    ui.editor.style.fontFamily = "'" + ((note && note.font) || settings.font || "맑은 고딕") + "', 'Segoe UI', sans-serif";
    ui.editor.style.fontSize = ((note && note.font_size) || 16) + "px";
    ui.editor.contentEditable = locked ? "false" : "true";
    ui.editor.style.opacity = locked ? ".6" : "1";
    if (note && note.attachment_data) {
      ui.attach.src = note.attachment_data;
      ui.attach.classList.remove("hidden");
    } else {
      ui.attach.removeAttribute("src");
      ui.attach.classList.add("hidden");
    }
    [ui.title, ui.addBtn, ui.delBtn].forEach(function (node) { node.disabled = locked; });
    Array.prototype.forEach.call(ui.toolbar.querySelectorAll("button, select"), function (node) {
      node.disabled = locked;
    });
  }

  function selectNote(index) {
    flushEdits();
    active = index;
    render();
  }

  function noteFromEditor() {
    var note = notes[active];
    if (!note || !user) return false;
    var html = ui.editor.innerHTML === "<br>" ? "" : ui.editor.innerHTML;
    var title = (ui.title.value || "").slice(0, 6);
    if (html === (note.html || "") && title === (note.title || "")) return false;
    note.html = html;
    note.title = title || "NOTE";
    note.synced_at = null;   /* 로컬 수정 → 다시 올려야 함 */
    return true;
  }

  function flushEdits() {
    if (busy) return;
    if (noteFromEditor()) {
      renderRail();
      schedulePush();
    }
  }

  /* --------------------------------------------------------------- 저장/동기화 */
  function schedulePush() {
    if (pushTimer) clearTimeout(pushTimer);
    pushTimer = setTimeout(function () { pushTimer = null; pushDirty(); }, PUSH_DELAY);
  }

  function dirtyRows() {
    if (!user) return [];
    var rows = [];
    notes.forEach(function (note, index) {
      if (note.synced_at || tombstones[note.id]) return;
      rows.push(rowFromNote(note, index));
    });
    Object.keys(tombstones).forEach(function (noteId) {
      if (tombstones[noteId] !== true) return;   /* 서버가 확인해 준 삭제는 다시 보내지 않음 */
      rows.push({
        user_id: user.id, note_id: noteId, position: -1,
        payload: { id: noteId }, deleted: true
      });
    });
    return rows;
  }

  function postgrest() { return db.from(NOTES_TABLE); }

  function pushDirty() {
    if (!db || !user || busy) return Promise.resolve();
    var rows = dirtyRows();
    if (!rows.length) return Promise.resolve();
    busy = true;
    setStatus("동기화 중…");
    return postgrest()
      .upsert(rows, { onConflict: "user_id,note_id" })
      .select("note_id,deleted,updated_at")
      .then(function (res) {
        if (res.error) throw res.error;
        (res.data || []).forEach(function (row) {
          if (row.deleted) { tombstones[row.note_id] = row.updated_at || true; return; }
          var note = findNote(row.note_id);
          if (note) { note.synced_at = row.updated_at; delete tombstones[row.note_id]; }
        });
        setStatus("저장했습니다 · " + now());
      })
      .catch(function (err) { setStatus("동기화 실패: " + describe(err), true); })
      .then(function () { busy = false; });
  }

  function describe(err) {
    if (!err) return "알 수 없는 오류";
    var text = err.message || err.error_description || err.details || String(err);
    if (/relation .* does not exist/i.test(text)) return "테이블이 없습니다. supabase-schema.sql을 실행했는지 확인해 주세요.";
    if (/Invalid login credentials/i.test(text)) return "이메일 또는 비밀번호가 올바르지 않습니다.";
    if (/Email not confirmed/i.test(text)) return "가입 확인 메일의 링크를 먼저 눌러 주세요.";
    if (/row-level security/i.test(text)) return "로그인이 필요합니다.";
    if (/Failed to fetch|NetworkError/i.test(text)) return "네트워크에 연결하지 못했습니다.";
    if (/duplicate key/i.test(text)) return "이미 등록된 항목이 있습니다.";
    return text;
  }

  function pullAll() {
    if (!db || !user) return Promise.resolve();
    return postgrest()
      .select("note_id,position,payload,deleted,updated_at")
      .order("position", { ascending: true })
      .then(function (res) {
        if (res.error) throw res.error;
        applyRemoteRows(res.data || []);
      })
      .catch(function (err) { setStatus("불러오기 실패: " + describe(err), true); });
  }

  function applyRemoteRows(rows) {
    var changed = false;
    rows.forEach(function (row) {
      if (!row || !row.note_id) return;
      var local = findNote(row.note_id);
      if (row.deleted) {
        if (local) { notes.splice(notes.indexOf(local), 1); changed = true; }
        tombstones[row.note_id] = row.updated_at || true;
        return;
      }
      if (local && !local.synced_at) return;                    /* 로컬 수정 우선 */
      if (local && local.synced_at === row.updated_at) return;   /* 이미 같은 상태 */
      var incoming = noteFromRow(row);
      if (local) notes[notes.indexOf(local)] = incoming;
      else { notes.push(incoming); delete tombstones[row.note_id]; }
      changed = true;
    });
    if (!changed) return;
    notes.forEach(function (n, i) { n.tab_slot = (i + 1) + "번 칸"; });
    if (!notes.length) notes.push(blankNote(0));
    if (active < 0 || active >= notes.length) active = 0;
    render();
  }

  function pullSettings() {
    if (!db || !user) return Promise.resolve();
    return db.from(SETTINGS_TABLE).select("payload,updated_at").limit(1)
      .then(function (res) {
        if (res.error) throw res.error;
        if (res.data && res.data[0] && res.data[0].payload) settings = res.data[0].payload;
      })
      .catch(function () { /* 설정이 없어도 동작합니다. */ });
  }

  function syncNow() {
    if (!user) { ui.dlg.showModal(); return Promise.resolve(); }
    return pullAll()
      .then(function () { return pushDirty(); })
      .then(function () {
        notes.forEach(function (n) { n.synced_at = null; });
        return pushDirty();
      })
      .then(function () { setStatus("동기화 완료 · " + now()); });
  }

  /* ------------------------------------------------------------------ 실시간 */
  function subscribe() {
    if (channel) { db.removeChannel(channel); channel = null; }
    channel = db.channel("sidememo-web")
      .on("postgres_changes", { event: "*", schema: "public", table: NOTES_TABLE },
        function (payload) {
          var row = payload.new || payload.old;
          if (!row || (row.user_id && row.user_id !== user.id)) return;
          applyRemoteRows([row]);
        })
      .subscribe();
  }

  /* ------------------------------------------------------------------ 로그인 */
  function showLoginError(msg) {
    ui.loginErr.textContent = msg;
    ui.loginErr.classList.remove("hidden");
  }

  function renderAccount() {
    var signed = !!user;
    ui.account.textContent = signed ? (user.email || "로그인됨") : "로그인되지 않음";
    ui.loginBtn.classList.toggle("hidden", signed);
    ui.logoutBtn.classList.toggle("hidden", !signed);
    renderEditor();
  }

  function afterSignIn() {
    renderAccount();
    setStatus("메모를 불러오는 중…");
    pullSettings()
      .then(function () { return pullAll(); })
      .then(function () {
        if (!notes.length) {
          notes.push(blankNote(0));
          active = 0;
          render();
          return pushDirty();
        }
        if (active < 0 || active >= notes.length) active = 0;
        render();
      })
      .then(subscribe)
      .then(function () { setStatus("로그인했습니다 · " + now()); })
      .catch(function (err) { setStatus("동기화 실패: " + describe(err), true); });
  }

  function signIn(signUp) {
    var email = (ui.email.value || "").trim();
    var password = ui.password.value || "";
    ui.loginErr.classList.add("hidden");
    if (!email || password.length < 6) {
      showLoginError("이메일과 6자 이상의 비밀번호를 입력해 주세요.");
      return;
    }
    var call = signUp
      ? db.auth.signUp({ email: email, password: password })
      : db.auth.signInWithPassword({ email: email, password: password });
    call.then(function (res) {
      if (res.error) throw res.error;
      if (signUp && !res.data.session) {
        showLoginError("가입 확인 메일을 보냈습니다. 메일의 링크를 누른 뒤 로그인해 주세요.");
        return;
      }
      ui.dlg.close();
    }).catch(function (err) { showLoginError(describe(err)); });
  }

  function signOut() {
    if (channel) { db.removeChannel(channel); channel = null; }
    db.auth.signOut().then(function () {
      notes = []; tombstones = {}; active = -1; user = null;
      notes.push(blankNote(0)); active = 0;
      renderAccount(); render();
      setStatus("로그아웃했습니다.");
    });
  }

  /* -------------------------------------------------------------------- 툴바 */
  function exec(cmd, value) {
    ui.editor.focus();
    document.execCommand("styleWithCSS", false, true);
    document.execCommand(cmd, false, value || null);
    flushEdits();
  }

  function setFontSize(size) {
    var px = parseInt(size, 10);
    if (!px) return;
    var sel = window.getSelection();
    if (sel && sel.rangeCount && !sel.isCollapsed) {
      ui.editor.focus();
      document.execCommand("styleWithCSS", false, true);
      document.execCommand("fontSize", false, "7");
      Array.prototype.forEach.call(ui.editor.querySelectorAll('font[size="7"]'), function (node) {
        var span = document.createElement("span");
        span.style.fontSize = px + "px";
        while (node.firstChild) span.appendChild(node.firstChild);
        node.parentNode.replaceChild(span, node);
      });
    } else {
      ui.editor.focus();
      document.execCommand("insertHTML", false, '<span style="font-size:' + px + 'px">\u200b</span>');
    }
    flushEdits();
  }

  function insertChecklist() { exec("insertHTML", "☐&nbsp;"); }

  /* ------------------------------------------------------------------ 초기화 */
  function bindUi() {
    ui.connBtn.addEventListener("click", openConnDialog);
    ui.connSave.addEventListener("click", saveConn);
    ui.connClose.addEventListener("click", function () { ui.connDlg.close(); });
    ui.title.addEventListener("input", function () {
      if (noteFromEditor()) { renderRail(); schedulePush(); }
    });
    ui.editor.addEventListener("input", function () {
      if (noteFromEditor()) { renderRail(); schedulePush(); }
    });
    ui.editor.addEventListener("keydown", function (event) {
      /* 체크리스트 줄에서 Enter → 다음 줄에도 체크박스 */
      if (event.key !== "Enter" || event.shiftKey) return;
      var sel = window.getSelection();
      if (!sel || !sel.rangeCount) return;
      var range = sel.getRangeAt(0);
      if (!ui.editor.contains(range.startContainer)) return;
      var node = range.startContainer;
      if (node.nodeType === 3) node = node.parentNode;
      var text = "";
      while (node && node !== ui.editor) {
        if (node.nodeType === 1 && node.tagName === "DIV") { text = node.textContent || ""; break; }
        node = node.parentNode;
      }
      if (!text) text = ui.editor.textContent || "";
      if (text.trim().indexOf("☐") === 0) {
        event.preventDefault();
        exec("insertHTML", "☐&nbsp;");
      }
    });
    ui.addBtn.addEventListener("click", function () {
      flushEdits();
      notes.push(blankNote(notes.length));
      active = notes.length - 1;
      render(); schedulePush();
    });
    ui.delBtn.addEventListener("click", function () {
      var note = notes[active];
      if (!note) return;
      if (!window.confirm('"' + (note.title || "메모") + '" 메모를 삭제할까요?')) return;
      notes.splice(active, 1);
      tombstones[note.id] = true;
      if (!notes.length) notes.push(blankNote(0));
      active = Math.max(0, Math.min(active, notes.length - 1));
      notes.forEach(function (n, i) { n.tab_slot = (i + 1) + "번 칸"; });
      render();
      pushDirty();
    });
    Array.prototype.forEach.call(ui.toolbar.querySelectorAll("button[data-cmd]"), function (button) {
      button.addEventListener("mousedown", function (e) { e.preventDefault(); });
      button.addEventListener("click", function () { exec(button.getAttribute("data-cmd")); });
    });
    ui.checkBtn.addEventListener("mousedown", function (e) { e.preventDefault(); });
    ui.checkBtn.addEventListener("click", insertChecklist);
    ui.fontSize.addEventListener("change", function () { setFontSize(ui.fontSize.value); });
    ui.syncBtn.addEventListener("click", function () { syncNow(); });
    ui.loginBtn.addEventListener("click", function () { ui.loginErr.classList.add("hidden"); ui.dlg.showModal(); });
    ui.logoutBtn.addEventListener("click", signOut);
    ui.signinBtn.addEventListener("click", function () { signIn(false); });
    ui.signupBtn.addEventListener("click", function () { signIn(true); });
    ui.closeDlg.addEventListener("click", function () { ui.dlg.close(); });
    ui.dlg.addEventListener("submit", function (e) { e.preventDefault(); signIn(false); });
    ui.themeBtn.addEventListener("click", function () {
      document.body.classList.toggle("dark");
      var dark = document.body.classList.contains("dark");
      ui.themeBtn.textContent = dark ? "라이트 모드" : "다크 모드";
      try { localStorage.setItem("sidememo-theme", dark ? "dark" : "light"); } catch (e) {}
    });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && user) syncNow();
    });
    window.addEventListener("beforeunload", flushEdits);
  }

  function connectDb() {
    db = window.supabase.createClient(CFG.supabaseUrl, CFG.supabaseAnonKey);
    ui.ver.textContent = "웹 " + (CFG.version || "1.0.6");
    db.auth.getSession().then(function (res) {
      var session = res.data ? res.data.session : null;
      user = session ? session.user : null;
      if (user) afterSignIn();
      else { renderAccount(); setStatus("로그인하면 메모가 자동으로 동기화됩니다."); }
    });
    db.auth.onAuthStateChange(function (event, session) {
      setTimeout(function () {
        var next = session ? session.user : null;
        var wasSigned = !!user;
        user = next;
        if (next && !wasSigned) { notes = []; active = -1; afterSignIn(); }
        else if (!next) { renderAccount(); }
      }, 0);
    });
  }

  function openConnDialog() {
    ui.connErr.classList.add("hidden");
    ui.connUrl.value = CFG.supabaseUrl || "";
    ui.connKey.value = CFG.supabaseAnonKey || "";
    ui.connDlg.showModal();
  }

  function saveConn() {
    var url = (ui.connUrl.value || "").trim().replace(/\/+$/, "");
    var key = (ui.connKey.value || "").trim();
    if (!url || !key) {
      ui.connErr.textContent = "주소와 anon key를 모두 입력해 주세요.";
      ui.connErr.classList.remove("hidden");
      return;
    }
    saveConfig(url, key);
    CFG = { supabaseUrl: url, supabaseAnonKey: key, version: "1.0.6" };
    ui.connDlg.close();
    setStatus("연결 정보를 저장했습니다. 새로 고치는 중…");
    setTimeout(function () { window.location.reload(); }, 400);
  }

  function init() {
    try {
      if (localStorage.getItem("sidememo-theme") === "dark") {
        document.body.classList.add("dark");
        ui.themeBtn.textContent = "라이트 모드";
      }
    } catch (e) {}
    bindUi();
    notes = [];
    active = -1;
    renderAccount();
    render();

    if (!CFG.supabaseUrl || !CFG.supabaseAnonKey) {
      notes = [blankNote(0)];
      active = 0;
      render();
      ui.ver.textContent = "설정 필요";
      setStatus("오른쪽 위의 [연결 설정]을 눌러 Supabase 주소와 anon key를 입력해 주세요.", true);
      openConnDialog();
      return;
    }
    if (!window.supabase || !window.supabase.createClient) {
      notes = [blankNote(0)];
      active = 0;
      render();
      ui.ver.textContent = "오프라인";
      setStatus("Supabase 라이브러리를 불러오지 못했습니다. 인터넷 연결을 확인해 주세요.", true);
      return;
    }
    connectDb();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

