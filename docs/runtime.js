/* Template runtime for the SuperF dashboard.
 *
 * The prototype was authored as a component for a template runtime that was not
 * shipped with the file, so the markup cannot render on its own. This is that
 * runtime, reimplemented for eight directives and nothing else, so the markup
 * and renderVals() stay byte-identical to what design handed over:
 *
 *   {{path}}              interpolation, in text and attribute values
 *   <sc-for list as>      repeat children with the item bound into scope
 *   <sc-if value>         render children when truthy
 *   <sc-raw-table>        table thead tbody tr th td select
 *   sc-camel-on-click     addEventListener('click', handler)
 *   sc-camel-on-change    addEventListener('change', handler)
 *   hint-*                authoring hints, ignored
 *
 * `sc-camel-on-*` is generic — `sc-camel-on-keydown` binds keydown — and one
 * attribute of this runtime's own, `data-focus-key`, survives a re-render:
 * see render() below.
 *
 * Table parts are authored as sc-raw-* precisely so the HTML parser does not
 * apply table scoping rules to the template; they become real elements here.
 */

const RAW_TAGS = {
  "sc-raw-table": "table",
  "sc-raw-thead": "thead",
  "sc-raw-tbody": "tbody",
  "sc-raw-tr": "tr",
  "sc-raw-th": "th",
  "sc-raw-td": "td",
  "sc-raw-select": "select",
};

const BINDING = /\{\{([^}]*)\}\}/g;
const WHOLE_BINDING = /^\s*\{\{([^}]*)\}\}\s*$/;

function resolve(path, scope) {
  const trimmed = String(path).trim();
  if (trimmed === "") return undefined;
  let value = scope;
  for (const key of trimmed.split(".")) {
    if (value === null || value === undefined) return undefined;
    value = value[key];
  }
  return value;
}

/* The raw value when an attribute is exactly one binding — needed so handlers
 * arrive as functions and lists as arrays rather than as "[object Object]". */
function rawValue(expression, scope) {
  if (expression == null) return undefined;
  const whole = String(expression).match(WHOLE_BINDING);
  if (whole) return resolve(whole[1], scope);
  return interpolate(expression, scope);
}

function interpolate(text, scope) {
  if (text == null) return "";
  return String(text).replace(BINDING, (_, path) => {
    const value = resolve(path, scope);
    return value === null || value === undefined ? "" : String(value);
  });
}

function renderNode(node, scope, parent) {
  if (node.nodeType === Node.TEXT_NODE) {
    const text = interpolate(node.nodeValue, scope);
    if (text) parent.appendChild(document.createTextNode(text));
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return;

  const tag = node.tagName.toLowerCase();

  if (tag === "sc-for") {
    const list = rawValue(node.getAttribute("list"), scope);
    const alias = node.getAttribute("as") || "item";
    if (!Array.isArray(list)) return;
    list.forEach((item, index) => {
      const child = Object.create(scope);
      child[alias] = item;
      child[alias + "_index"] = index;
      node.childNodes.forEach((c) => renderNode(c, child, parent));
    });
    return;
  }

  if (tag === "sc-if") {
    if (rawValue(node.getAttribute("value"), scope)) {
      node.childNodes.forEach((c) => renderNode(c, scope, parent));
    }
    return;
  }

  const realTag = RAW_TAGS[tag] || tag;
  const element = document.createElement(realTag);
  let selectValue;

  for (const attribute of Array.from(node.attributes)) {
    const name = attribute.name;
    if (name.startsWith("hint-") || name === "list" || name === "as") continue;

    if (name.startsWith("sc-camel-on-")) {
      const handler = rawValue(attribute.value, scope);
      if (typeof handler === "function") {
        element.addEventListener(name.slice("sc-camel-on-".length), handler);
      }
      continue;
    }
    if (realTag === "select" && name === "value") {
      selectValue = rawValue(attribute.value, scope);
      continue;
    }
    element.setAttribute(name, interpolate(attribute.value, scope));
  }

  node.childNodes.forEach((c) => renderNode(c, scope, element));
  if (selectValue !== undefined) element.value = selectValue;
  parent.appendChild(element);
}

/* Full re-render. At eight managers this is cheap, and it keeps the view a pure
 * function of renderVals() — the property the prototype was written against.
 *
 * The cost of replacing the whole tree is that focus lands on <body> after
 * every interaction, so a keyboard user who toggles the theme is returned to
 * the top of the page and a screen reader loses its place. Any element
 * carrying `data-focus-key` gets its focus back, which covers the controls
 * that cause a render in the first place; the select fallback below keeps the
 * You/Compare pickers alive through the unprompted 30s tick even if nobody
 * remembered to key them.
 */
export function render(root, template, values) {
  const active = document.activeElement;
  const inRoot = active && root.contains(active);
  const focusKey = inRoot ? active.getAttribute("data-focus-key") : null;
  const selects = Array.from(root.querySelectorAll("select"));
  const focusedIndex = selects.indexOf(active);

  const next = document.createDocumentFragment();
  template.content.childNodes.forEach((node) => renderNode(node, values, next));
  root.replaceChildren(next);

  const keyed = focusKey && root.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`);
  if (keyed) {
    keyed.focus();
  } else if (focusedIndex >= 0) {
    const restored = root.querySelectorAll("select")[focusedIndex];
    if (restored) restored.focus();
  }
}
