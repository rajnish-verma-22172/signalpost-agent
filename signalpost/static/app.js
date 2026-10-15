"use strict";

const $ = (selector) => document.querySelector(selector);
let profiles = [];
let busy = false;
const labels = {
  registration_number: "Registration number", name: "Company name",
  organization_form: "Organization form", business_address: "Business address",
  postal_address: "Postal address", industry: "Primary industry", website: "Website",
  employees: "Employees", founded: "Founded", registered: "Registry registration date",
  vat_registered: "VAT registered", bankrupt: "Bankrupt", in_liquidation: "In liquidation",
  latest_accounts_year: "Latest filed accounts year"
};
const statusLabels = {
  ok: "Verified identity", invalid: "Invalid number", missing: "Not found",
  ambiguous: "Identity mismatch", blocked: "Access blocked", failed: "Lookup failed"
};

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function display(value) {
  if (value === null || value === undefined) return "Not available";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}

function timestamp(value) {
  return value ? new Date(value).toLocaleString() : "Not checked";
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed.");
  return data;
}

function setBusy(value) {
  busy = value;
  document.querySelectorAll("button").forEach((button) => { button.disabled = value; });
  $("#results").setAttribute("aria-busy", String(value));
}

function render() {
  const container = $("#results");
  container.replaceChildren();
  $("#count").textContent = `${profiles.length} profile${profiles.length === 1 ? "" : "s"}`;
  if (!profiles.length) {
    container.append(element("p", "No profiles yet. Enter a company number above to begin.", "empty"));
  }
  for (const profile of profiles) {
    const card = element("article", undefined, "panel company");
    const heading = element("div", undefined, "profile-heading");
    const title = element("div");
    title.append(element("p", profile.registration_number, "eyebrow"),
      element("h3", profile.facts.name?.value || "Company lookup"));
    heading.append(title, element("span", statusLabels[profile.status] || profile.status, `badge ${profile.status}`));
    card.append(heading, element("p", profile.message),
      element("p", `Last attempt: ${timestamp(profile.checked_at)}${profile.cached ? " · Saved result" : ""}`, "muted"));
    if (profile.stale) card.append(element("p", "Refresh did not succeed. Facts below are last-known data, not newly verified.", "warning"));
    const refresh = element("button", "Refresh this company", "secondary");
    refresh.type = "button";
    refresh.disabled = busy;
    refresh.addEventListener("click", () => run([profile.registration_number], true));
    card.append(refresh);
    const facts = element("div", undefined, "facts");
    for (const [field, fact] of Object.entries(profile.facts)) {
      const row = element("div", undefined, "fact");
      row.append(element("h4", labels[field] || field), element("pre", display(fact.value), "value"));
      const evidence = element("details");
      evidence.append(element("summary", `Evidence · retrieved ${timestamp(fact.evidence.retrieved_at)}`));
      const link = element("a", fact.evidence.source);
      // Only the fixed public registry origin is navigable, even for imported data.
      try {
        const url = new URL(fact.evidence.url);
        if (url.protocol === "https:" && url.hostname === "data.brreg.no") link.href = url.href;
      } catch { /* A malformed source remains visible text, not a link. */ }
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      evidence.append(link, element("pre", fact.evidence.snippet, "snippet"),
        element("p", `First seen: ${timestamp(fact.first_seen)} · Last confirmed: ${timestamp(fact.last_seen)}`, "muted"));
      row.append(evidence);
      facts.append(row);
    }
    card.append(facts);
    const missing = Object.keys(labels).filter((field) => !(field in profile.facts));
    if (missing.length) card.append(element("p", `Unavailable: ${missing.map((field) => labels[field]).join(", ")}. Not inferred.`, "muted"));
    if (profile.history.length) {
      const history = element("details", undefined, "history");
      history.append(element("summary", `Refresh history (${profile.history.length} runs)`));
      for (const run of profile.history) {
        const entry = element("div", undefined, "run");
        entry.append(element("strong", `${timestamp(run.checked_at)} · ${run.status}`));
        entry.append(element("p", run.message));
        if (!run.changes.length) entry.append(element("p",
          run.status === "ok" ? "No fact changes." : "No new facts accepted; last-known data retained.", "muted"));
        for (const change of run.changes) {
          entry.append(element("pre", `${labels[change.field] || change.field} (${change.kind}): ${display(change.before)} → ${display(change.after)}`, "snippet"));
        }
        history.append(entry);
      }
      card.append(history);
    }
    container.append(card);
  }
}

async function run(numbers, refresh = false) {
  if (busy) return;
  if (!numbers.length) {
    $("#notice").textContent = "Enter at least one registration number, or look up profiles before refreshing.";
    return;
  }
  setBusy(true);
  $("#notice").textContent = "Checking companies… Large batches can take several minutes. Keep this page open.";
  try {
    const data = await api(refresh ? "/api/refresh" : "/api/lookup", {numbers});
    const updated = new Map(profiles.map((profile) => [profile.registration_number, profile]));
    data.results.forEach((profile) => updated.set(profile.registration_number, profile));
    profiles = [...updated.values()];
    render();
    const verified = data.results.filter((profile) => profile.status === "ok").length;
    $("#notice").textContent = `Finished ${data.results.length} lookups: ${verified} verified; ${data.results.length - verified} need attention.`;
  } catch (error) {
    $("#notice").textContent = `Could not complete the request: ${error.message} Saved data has not been cleared.`;
  } finally {
    setBusy(false);
  }
}

$("#lookup-form").addEventListener("submit", (event) => {
  event.preventDefault();
  run($("#numbers").value.trim().split(/[\s,;]+/).filter(Boolean));
});
$("#refresh-all").addEventListener("click", () => run(profiles.filter((p) => p.status !== "invalid").map((p) => p.registration_number), true));
$("#export").addEventListener("click", () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify({results: profiles}, null, 2)], {type: "application/json"}));
  const link = element("a");
  link.href = url;
  link.download = "signalpost-profiles.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

async function initialize() {
  setBusy(true);
  try {
    const [config, saved] = await Promise.all([api("/api/config"), api("/api/companies")]);
    $("#source").textContent = `Source: ${config.source}`;
    profiles = saved.companies;
    render();
  } catch (error) {
    $("#notice").textContent = `Could not load saved profiles: ${error.message} Please reload or restart the server.`;
  } finally {
    setBusy(false);
  }
}
initialize();
