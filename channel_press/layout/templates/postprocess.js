(function () {
  "use strict";

  function pxToPt(px) {
    return Math.round((parseFloat(px) * 0.75 + Number.EPSILON) * 10) / 10;
  }

  function textOf(el) {
    return (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
  }

  async function run() {
    if (document.fonts && document.fonts.ready) {
      try { await document.fonts.ready; } catch (e) { /* ignore */ }
    }
    // The polyfill starts pagination itself. Do not call preview() a second
    // time: the second call waits on an already-fired render promise.
    // Wait until the page count has been stable for a few ticks.
    let stable = 0;
    let last = -1;
    for (let i = 0; i < 300; i += 1) {
      const count = document.querySelectorAll(".pagedjs_page").length;
      if (count > 0 && count === last) { stable += 1; } else { stable = 0; }
      last = count;
      if (stable >= 3) { break; }
      await new Promise(function (resolve) { setTimeout(resolve, 100); });
    }
    const images = Array.prototype.slice.call(document.images);
    await Promise.all(images.map(function (img) {
      if (img.complete) { return Promise.resolve(); }
      return new Promise(function (resolve) { img.addEventListener("load", resolve); img.addEventListener("error", resolve); });
    }));
    await new Promise(function (resolve) { setTimeout(resolve, 120); });

    const pages = Array.prototype.slice.call(document.querySelectorAll(".pagedjs_page"));
    const storyIndex = new Map();
    const pageRecords = [];

    pages.forEach(function (pageEl, idx) {
      const number = idx + 1;
      pageEl.setAttribute("data-page-number", String(number));
      const content = pageEl.querySelector(".pagedjs_page_content") || pageEl;
      const stories = Array.prototype.slice.call(content.querySelectorAll(".story"));
      let template = "interior";
      if (content.querySelector(".masthead") || content.querySelector(".story--front")) {
        template = "front_major_event";
      } else if (content.querySelector(".source-table")) {
        template = "sources";
      } else if (stories.length) {
        template = stories[0].getAttribute("data-template") || "national";
      }
      const sectionEl = content.querySelector("[data-section]");
      const record = {
        page: number,
        template: template,
        section: sectionEl ? sectionEl.getAttribute("data-section") : "",
        stories: [],
        blank: false,
        min_font_pt: 99,
        chars: textOf(content).length,
        overflow: [],
        images: 0
      };
      stories.forEach(function (storyEl) {
        const id = storyEl.getAttribute("data-story-id") || "";
        const articleId = storyEl.getAttribute("data-article-id") || "";
        const chunk = parseInt(storyEl.getAttribute("data-chunk") || "1", 10);
        record.stories.push({ story_id: id, article_id: articleId, chunk: chunk });
        if (!storyIndex.has(id)) { storyIndex.set(id, []); }
        storyIndex.get(id).push({ page: number, chunk: chunk, el: storyEl });
      });

      const pageRect = pageEl.getBoundingClientRect();
      const contentRect = content.getBoundingClientRect();
      const all = Array.prototype.slice.call(content.querySelectorAll("*"));
      let usedBottom = contentRect.top;
      all.forEach(function (el) {
        const tag = (el.tagName || "").toLowerCase();
        if (tag === "script" || tag === "style" || tag === "svg" || tag === "path") { return; }
        // Paged.js wraps split flow content in an anonymous div whose width is
        // the full multi-column measure rather than the printed column. The
        // visible children are measured on their own below, so the wrapper must
        // not be reported as page overflow.
        if (!(el.className || "").toString().trim() && !el.id && el.children.length === 1) {
          const only = el.firstElementChild;
          if (only && (only.hasAttribute("data-ref") || only.hasAttribute("data-split-from") || only.hasAttribute("data-split-to"))) { return; }
        }
        const hasText = el.children.length === 0 && textOf(el).length > 0;
        const isMedia = tag === "img" || tag === "svg" || tag === "figure" || tag === "table" || tag === "canvas";
        if ((hasText || isMedia) && !el.closest(".front-footer")) {
          const contentRectForMeasure = el.getBoundingClientRect();
          if (contentRectForMeasure.bottom > usedBottom) {
            usedBottom = Math.min(contentRectForMeasure.bottom, contentRect.bottom);
          }
        }
        if (hasText) {
          const fs = parseFloat(window.getComputedStyle(el).fontSize || "0");
          if (fs > 0) {
            const pt = pxToPt(fs);
            if (pt < record.min_font_pt) { record.min_font_pt = pt; }
          }
        }
        if (el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0) {
          const style = window.getComputedStyle(el);
          if (style.overflowX === "visible") {
            record.overflow.push({
              kind: "horizontal",
              tag: tag,
              cls: el.className && el.className.toString().slice(0, 60),
              text: textOf(el).slice(0, 60),
              over_px: Math.round(el.scrollWidth - el.clientWidth)
            });
          }
        }
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
          if (r.right > contentRect.right + 3 || r.left < contentRect.left - 3 ||
              r.bottom > contentRect.bottom + 3 || r.top < contentRect.top - 3) {
            record.overflow.push({
              kind: "outside",
              tag: tag,
              cls: (el.className || "").toString().slice(0, 60),
              text: textOf(el).slice(0, 60),
              over_px: Math.round(Math.max(
                r.right - contentRect.right,
                contentRect.left - r.left,
                r.bottom - contentRect.bottom,
                contentRect.top - r.top
              ))
            });
          }
        }
      });
      Array.prototype.slice.call(content.querySelectorAll("img")).forEach(function (img) {
        record.images += 1;
        if (!img.complete || img.naturalWidth === 0) {
          record.overflow.push({ kind: "image_missing", src: (img.getAttribute("src") || "").slice(0, 120) });
        }
      });
      if (record.min_font_pt === 99) { record.min_font_pt = 0; }
      record.blank = record.chars < 40 && record.images === 0;
      record.width_px = Math.round(pageRect.width);
      record.height_px = Math.round(pageRect.height);
      const contentHeight = Math.max(0, contentRect.height);
      const usedHeight = Math.max(0, usedBottom - contentRect.top);
      record.content_height_px = Math.round(contentHeight);
      record.used_height_px = Math.round(usedHeight);
      record.fill_ratio = contentHeight > 0
        ? Math.round(Math.max(0, Math.min(1, usedHeight / contentHeight)) * 1000) / 1000
        : 0;
      record.trailing_whitespace_px = Math.round(Math.max(0, contentRect.bottom - usedBottom));
      record.underfull = !record.blank && record.chars >= 40 && record.fill_ratio < 0.62;
      record.orphan_section_head = template !== "sources" &&
        Boolean(content.querySelector(".section-head")) && stories.length === 0;
      pageRecords.push(record);
    });

    // Appendix page reference, filled only where the appendix really rendered.
    let appendixPage = 0;
    pageRecords.forEach(function (p) { if (p.template === "sources") { appendixPage = p.page; } });
    if (appendixPage) {
      Array.prototype.slice.call(document.querySelectorAll(".appendix-page")).forEach(function (el) {
        el.textContent = String(appendixPage);
      });
    }

    // Continuation labels with validated page references.
    storyIndex.forEach(function (entries) {
      entries.sort(function (a, b) { return a.page - b.page || a.chunk - b.chunk; });
      entries.forEach(function (entry, i) {
        if (entry.chunk > 1) {
          let previous = null;
          for (let j = 0; j < entries.length; j += 1) {
            if (entries[j].page < entry.page || (entries[j].page === entry.page && entries[j].chunk < entry.chunk)) {
              previous = entries[j];
            }
          }
          if (previous) {
            const label = document.createElement("div");
            label.className = "continuation-from";
            label.textContent = "Continued from page " + previous.page;
            entry.el.insertBefore(label, entry.el.firstChild);
          }
        }
        const marker = entry.el.querySelector(".continues");
        if (marker) {
          const wanted = parseInt(marker.getAttribute("data-next-chunk") || "0", 10);
          let next = null;
          for (let j = 0; j < entries.length; j += 1) {
            if (entries[j].chunk === wanted && entries[j].page >= entry.page) { next = entries[j]; break; }
          }
          if (next) {
            const ref = marker.querySelector(".page-ref");
            if (ref) { ref.textContent = String(next.page); }
          } else {
            marker.style.display = "none";
          }
        }
      });
    });

    // Front rail: point every promoted brief at the page where its report runs.
    Array.prototype.slice.call(document.querySelectorAll(".front-brief")).forEach(function (brief) {
      const id = brief.getAttribute("data-story-id");
      const entries = storyIndex.get(id);
      if (!entries || !entries.length) { return; }
      const target = entries.filter(function (e) { return e.page > 1; })[0] || entries[0];
      const span = document.createElement("div");
      span.className = "front-brief__page";
      span.textContent = "Full report, page " + target.page;
      brief.appendChild(span);
    });

    const firstPage = pageRecords[0] || {};
    window.__layoutMeta = {
      pagedReady: true,
      width_px: firstPage.width_px || 0,
      height_px: firstPage.height_px || 0,
      page_count: pageRecords.length,
      appendix_page: appendixPage,
      pages: pageRecords,
      story_pages: Array.from(storyIndex.entries()).map(function (pair) {
        return { story_id: pair[0], pages: pair[1].map(function (e) { return { page: e.page, chunk: e.chunk }; }) };
      })
    };
    window.__pagedReady = true;
  }

  run().catch(function (err) {
    window.__pagedError = String(err && err.stack ? err.stack : err);
    window.__pagedReady = true;
  });
})();
