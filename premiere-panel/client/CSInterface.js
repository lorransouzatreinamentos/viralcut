/**
 * CSInterface — minimal shim for CEP host communication.
 * Based on Adobe's CEP 9+ public API surface. Only methods used by FASTVIDEO.
 */
function CSInterface() {}

CSInterface.prototype.getHostEnvironment = function() {
    return JSON.parse(window.__adobe_cep__.getHostEnvironment());
};

CSInterface.prototype.evalScript = function(script, callback) {
    callback = callback || function() {};
    if (typeof window.__adobe_cep__ !== 'undefined') {
        window.__adobe_cep__.evalScript(script, callback);
    } else {
        // Dev mode without CEP host — simulate
        console.warn('[CSInterface] No host, dry-run evalScript:', script);
        callback('{"ok":true,"dryRun":true}');
    }
};

CSInterface.prototype.getSystemPath = function(pathType) {
    return window.__adobe_cep__ ? window.__adobe_cep__.getSystemPath(pathType) : '';
};

CSInterface.prototype.addEventListener = function(type, listener) {
    if (window.__adobe_cep__) {
        window.__adobe_cep__.addEventListener(type, listener);
    }
};

CSInterface.prototype.openURLInDefaultBrowser = function(url) {
    if (typeof cep !== 'undefined' && cep.util) {
        cep.util.openURLInDefaultBrowser(url);
    } else {
        window.open(url, '_blank');
    }
};

window.CSInterface = CSInterface;
