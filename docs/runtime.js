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
 * Focus is preserved so the You/Compare pickers survive the 30s tick. */
export function render(root, template, values) {
  const active = document.activeElement;
  const selects = Array.from(root.querySelectorAll("select"));
  const focusedIndex = selects.indexOf(active);

  const next = document.createDocumentFragment();
  template.content.childNodes.forEach((node) => renderNode(node, values, next));
  root.replaceChildren(next);

  if (focusedIndex >= 0) {
    const restored = root.querySelectorAll("select")[focusedIndex];
    if (restored) restored.focus();
  }
}
