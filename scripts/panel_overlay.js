/* AE Localizer UI 1.4.0. No prototype hooks, remote requests, or value changes. */
(function () {
    'use strict';
    if (window.__aelUI140) return;
    window.__aelUI140 = true;
    var dict = /*DICTIONARY*/{};
    var attrs = ['title','placeholder','aria-label','aria-description','alt','data-tooltip','data-tip','data-hint','data-original-title','data-bs-title'];
    var tags = /^(SCRIPT|STYLE|NOSCRIPT|CODE|PRE|SVG|CANVAS|IFRAME)$/;
    var queue = [], timer = null;
    function skip(el, attribute) {
        while (el && el.nodeType === 1) {
            if (tags.test(el.tagName) || !attribute && /^(TEXTAREA|INPUT)$/.test(el.tagName) || el.isContentEditable || el.getAttribute('contenteditable') !== null && el.getAttribute('contenteditable') !== 'false' || /monaco|ace_editor|CodeMirror/.test(el.className || '')) return true;
            el = el.parentElement;
        }
        return false;
    }
    function translate(value) {
        if (!value || !value.trim()) return value;
        var key = value.trim();
        return Object.prototype.hasOwnProperty.call(dict,key) ? value.replace(key, function () { return dict[key]; }) : value;
    }
    function text(node) {
        if (skip(node.parentElement) || node.nodeValue === node.__aelLast140) return;
        var next = translate(node.nodeValue);
        if (next !== node.nodeValue) { node.__aelLast140 = next; node.nodeValue = next; }
    }
    function attributes(node) {
        if (skip(node,true)) return;
        var last = node.__aelAttrs140 || (node.__aelAttrs140 = {});
        attrs.forEach(function (name) {
            var value = node.getAttribute(name);
            if (!value || value === last[name]) return;
            var next = translate(value);
            if (value !== next) { last[name] = next; node.setAttribute(name,next); }
        });
    }
    function walk(root) {
        if (root.nodeType === 3) { text(root); return; }
        if (root.nodeType !== 1 || skip(root,true)) return;
        attributes(root);
        var walker = document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT,{
            acceptNode: function (node) { return node.nodeType === 1 && skip(node,true) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT; }
        },false), node;
        while ((node = walker.nextNode())) { if (node.nodeType === 3) text(node); else attributes(node); }
    }
    function enqueue(node) {
        if (queue.indexOf(node) < 0) queue.push(node);
        if (timer !== null) return;
        timer = setTimeout(function () {
            timer = null;var roots = queue;queue = [];
            roots.forEach(function (root) {
                if (document.documentElement.contains(root) && !roots.some(function (other) { return other !== root && other.contains && other.contains(root); })) walk(root);
            });
        },40);
    }
    function init() {
        walk(document.documentElement);
        var observer = new MutationObserver(function (changes) {
            changes.forEach(function (m) {
                if (m.type === 'characterData') enqueue(m.target);
                else if (m.type === 'attributes') enqueue(m.target);
                else Array.prototype.forEach.call(m.addedNodes,enqueue);
            });
        });
        observer.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:attrs});
        window.addEventListener('unload',function () { observer.disconnect();if (timer !== null) clearTimeout(timer);queue=[]; });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',init);else init();
})();
