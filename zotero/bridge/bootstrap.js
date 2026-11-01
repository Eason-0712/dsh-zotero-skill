/* global Zotero */
// Inherit Zotero's own server-id, API-key and local-API-enabled checks.
// No new server, credentials, database access, writes or remote requests.
var ENDPOINT = '/api/dsh-reader/selection';
var SelectionEndpoint;

async function startup() {
  await Zotero.initializationPromise;
  if (!Zotero.Server.LocalAPI?.Items) throw new Error('DSH Reader requires Zotero local API write authorization support');
  if (Zotero.Server.Endpoints[ENDPOINT]) throw new Error('DSH Reader selection endpoint already registered');
  SelectionEndpoint = class extends Zotero.Server.LocalAPI.Items {
    supportedMethods = ['POST'];
    supportedDataTypes = ['application/json'];
    permitBookmarklet = false;
    async run() {
      const pane = Zotero.getActiveZoteroPane();
      if (!pane) return [409, 'text/plain', 'Open a Zotero library window first'];
      const selected = pane.getSelectedItems();
      const rows = [];
      const seen = new Set();
      for (let item of selected) {
        // Attachments map to their parent paper; standalone PDF attachments remain usable.
        if (!item.isRegularItem() && item.parentID) item = await Zotero.Items.getAsync(item.parentID);
        if (!item.isRegularItem() && !item.isFileAttachment()) continue;
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        const library = Zotero.Libraries.get(item.libraryID);
        const api_prefix = library.libraryType === 'group'
          ? `/api/groups/${Zotero.Groups.getGroupIDFromLibraryID(item.libraryID)}` : '/api/users/0';
        rows.push({ item: await item.toResponseJSON(), api_prefix });
      }
      return [200, 'application/json', JSON.stringify(rows)];
    }
  };
  Zotero.Server.Endpoints[ENDPOINT] = SelectionEndpoint;
}

function shutdown() {
  if (SelectionEndpoint && Zotero.Server.Endpoints[ENDPOINT] === SelectionEndpoint) {
    delete Zotero.Server.Endpoints[ENDPOINT];
  }
  SelectionEndpoint = undefined;
}

function install() {}
function uninstall() {}
