// ============================================================================
// EARNINGS BOARD — Scriptable widget za iPhone
// ----------------------------------------------------------------------------
// 1. Instaliraj Scriptable iz App Storea (besplatno)
// 2. Otvori Scriptable → "+" → zalijepi ovu skriptu → nazovi je "Earnings"
// 3. Dolje promijeni FEED_URL u svoj GitHub Pages link
// 4. Home screen → drži prst → "+" → Scriptable → odaberi velicinu
// 5. Drzi prst na widgetu → "Edit Widget" → Script: Earnings
// ============================================================================

// ============================================================================
// EARNINGS BOARD — Scriptable widget za iPhone
// ----------------------------------------------------------------------------
// 1. Instaliraj Scriptable iz App Storea (besplatno)
// 2. Otvori Scriptable → "+" → zalijepi ovu skriptu → nazovi je "Earnings"
// 3. Dolje promijeni FEED_URL u svoj GitHub Pages link
// 4. Home screen → drži prst → "+" → Scriptable → odaberi velicinu
// 5. Drzi prst na widgetu → "Edit Widget" → Script: Earnings
// ============================================================================

const FEED_URL = "https://KORISNIK.github.io/REPO/earnings.json";

// --- paleta (departure board) -----------------------------------------------
const C = {
  bgTop: new Color("#141A23"),
  bgBottom: new Color("#0C1017"),
  amber: new Color("#F0A500"),
  paper: new Color("#E8E4DA"),
  slate: new Color("#6B7785"),
  alert: new Color("#E5484D"),
  soon: new Color("#F0A500"),
  rule: new Color("#232C38"),
};

const FAMILY = config.widgetFamily || "medium";
const ROWS = { small: 3, medium: 4, large: 9 }[FAMILY] || 4;

// --- dohvat ------------------------------------------------------------------
async function loadFeed() {
  const req = new Request(FEED_URL);
  req.timeoutInterval = 15;
  return await req.loadJSON();
}

function cacheFile() {
  const fm = FileManager.local();
  return fm.joinPath(fm.cacheDirectory(), "earnings-feed.json");
}

async function loadWithCache() {
  const fm = FileManager.local();
  const path = cacheFile();
  try {
    const data = await loadFeed();
    fm.writeString(path, JSON.stringify(data));
    return { data, stale: false };
  } catch (e) {
    if (fm.fileExists(path)) {
      return { data: JSON.parse(fm.readString(path)), stale: true };
    }
    throw e;
  }
}

// --- pomocne -----------------------------------------------------------------
function dayColor(days, estimated) {
  if (estimated) return C.slate;
  if (days <= 1) return C.alert;
  if (days <= 3) return C.soon;
  return C.paper;
}

function dayLabel(days) {
  if (days === 0) return "DANAS";
  if (days === 1) return "SUTRA";
  return `${days} d`;
}

function shortDate(iso) {
  const [, m, d] = iso.split("-");
  return `${parseInt(d, 10)}.${parseInt(m, 10)}.`;
}

function updatedLabel(generated) {
  const mins = Math.round((Date.now() - new Date(generated).getTime()) / 60000);
  if (mins < 60) return `${mins}m`;
  const h = Math.round(mins / 60);
  return h < 48 ? `${h}h` : `${Math.round(h / 24)}d`;
}

// --- crtanje -----------------------------------------------------------------
function buildWidget(feed, stale) {
  const w = new ListWidget();
  const grad = new LinearGradient();
  grad.colors = [C.bgTop, C.bgBottom];
  grad.locations = [0, 1];
  w.backgroundGradient = grad;
  w.setPadding(12, 13, 12, 13);
  w.url = FEED_URL.replace(/earnings\.json$/, "");

  // zaglavlje
  const head = w.addStack();
  head.centerAlignContent();

  const title = head.addText("EARNINGS");
  title.font = Font.semiboldSystemFont(FAMILY === "small" ? 10 : 11);
  title.textColor = C.amber;
  head.addSpacer();

  const meta = head.addText(
    (stale ? "⚠ " : "") + updatedLabel(feed.generated)
  );
  meta.font = Font.systemFont(9);
  meta.textColor = stale ? C.alert : C.slate;

  w.addSpacer(7);

  const items = (feed.items || []).slice(0, ROWS);

  if (items.length === 0) {
    w.addSpacer();
    const empty = w.addText("Nema objava u sljedecih 30 dana.");
    empty.font = Font.systemFont(11);
    empty.textColor = C.slate;
    empty.centerAlignText();
    w.addSpacer();
    return w;
  }

  items.forEach((item, i) => {
    if (i > 0) {
      const rule = w.addStack();
      rule.size = new Size(0, 1);
      rule.backgroundColor = C.rule;
      w.addSpacer(5);
    }

    const row = w.addStack();
    row.centerAlignContent();

    // ticker
    const sym = row.addText(item.symbol);
    sym.font = Font.boldSystemFont(FAMILY === "small" ? 12 : 14);
    sym.textColor = C.paper;
    sym.lineLimit = 1;

    // oznaka pomaka
    if (item.moved) {
      row.addSpacer(4);
      const mv = row.addText("↻");
      mv.font = Font.systemFont(10);
      mv.textColor = C.amber;
    }

    row.addSpacer();

    // datum + BMO/AMC (preskoci na maloj velicini)
    if (FAMILY !== "small") {
      const prefix = item.estimated ? "~" : "";
      const when = row.addText(`${prefix}${shortDate(item.date)} ${item.hourLabel}`);
      when.font = Font.systemFont(10);
      when.textColor = C.slate;
      row.addSpacer(8);
    }

    // odbrojavanje
    const cd = row.addText(dayLabel(item.days));
    cd.font = Font.semiboldSystemFont(FAMILY === "small" ? 11 : 12);
    cd.textColor = dayColor(item.days, item.estimated);
    cd.rightAlignText();

    if (i < items.length - 1) w.addSpacer(5);
  });

  w.addSpacer();
  return w;
}

// --- pokretanje ---------------------------------------------------------------
let widget;
try {
  const { data, stale } = await loadWithCache();
  widget = buildWidget(data, stale);
} catch (e) {
  widget = new ListWidget();
  widget.backgroundColor = C.bgBottom;
  const t = widget.addText("Feed nedostupan");
  t.font = Font.semiboldSystemFont(12);
  t.textColor = C.alert;
  const s = widget.addText(String(e).slice(0, 80));
  s.font = Font.systemFont(9);
  s.textColor = C.slate;
}

// osvjezi otprilike svaka 2 sata (iOS odlucuje o tocnom trenutku)
widget.refreshAfterDate = new Date(Date.now() + 2 * 60 * 60 * 1000);

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  await widget.presentMedium();
}
Script.complete();
