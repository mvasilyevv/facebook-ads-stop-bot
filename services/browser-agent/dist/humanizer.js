"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.generateHumanProfile = generateHumanProfile;
exports._resolveScrollAnchor = _resolveScrollAnchor;
exports.humanMove = humanMove;
exports.humanClick = humanClick;
exports.humanScrollToFind = humanScrollToFind;
exports.humanWheelScroll = humanWheelScroll;
/** Сгенерировать HumanProfile со случайными параметрами (один раз на сессию). */
function generateHumanProfile() {
    return {
        speedFactor: rand(0.7, 1.4),
        jitterFactor: rand(0.6, 1.5),
        pauseFactor: rand(0.7, 1.3),
        overshootChance: rand(0.1, 0.3),
        idleChance: rand(0.02, 0.08),
        idleDurationMin: rand(1.5, 2.5),
        idleDurationMax: rand(4.0, 6.0),
        bezierStepsMin: randInt(18, 24),
        bezierStepsMax: randInt(30, 40),
    };
}
function _perlinDelay(baseMin, baseMax, t, seed) {
    const noise = Math.sin(t * 7.3 + seed) * 0.3 + Math.sin(t * 13.1 + seed * 2.7) * 0.2;
    const mid = (baseMin + baseMax) / 2;
    const spread = (baseMax - baseMin) / 2;
    return Math.max(baseMin * 0.5, mid + spread * noise);
}
async function _resolveScrollAnchor(page) {
    try {
        const anchor = await page.evaluate(() => {
            const selectors = [
                '[role="grid"]',
                '[role="table"]',
                '[aria-rowcount]',
                '[data-surface*="table_row:"]',
            ];
            for (const selector of selectors) {
                const node = document.querySelector(selector);
                if (!(node instanceof Element))
                    continue;
                const rect = node.getBoundingClientRect();
                if (rect.width < 40 || rect.height < 40)
                    continue;
                return { x: rect.left + rect.width * 0.5, y: rect.top + rect.height * 0.5 };
            }
            return null;
        });
        if (typeof anchor === 'object' && anchor !== null && 'x' in anchor && 'y' in anchor) {
            return [Number(anchor.x), Number(anchor.y)];
        }
    }
    catch {
        // Отсутствие anchor не критично: ниже используем центр viewport.
    }
    return null;
}
function _bezierPoint(t, p0, p1, p2, p3) {
    const u = 1 - t;
    const x = u ** 3 * p0[0] + 3 * u ** 2 * t * p1[0] + 3 * u * t ** 2 * p2[0] + t ** 3 * p3[0];
    const y = u ** 3 * p0[1] + 3 * u ** 2 * t * p1[1] + 3 * u * t ** 2 * p2[1] + t ** 3 * p3[1];
    return [x, y];
}
function _bezierPath(start, end, steps, profile) {
    const dx = end[0] - start[0];
    const dy = end[1] - start[1];
    const dist = Math.hypot(dx, dy);
    const jf = profile.jitterFactor;
    const spread = Math.min(dist * 0.35 * jf, 120 * jf);
    const cp1 = [
        start[0] + dx * rand(0.2, 0.4) + rand(-spread, spread),
        start[1] + dy * rand(0.2, 0.4) + rand(-spread, spread),
    ];
    const cp2 = [
        start[0] + dx * rand(0.6, 0.8) + rand(-spread, spread),
        start[1] + dy * rand(0.6, 0.8) + rand(-spread, spread),
    ];
    const points = [];
    for (let i = 0; i <= steps; i++) {
        points.push(_bezierPoint(i / steps, start, cp1, cp2, end));
    }
    return points;
}
async function _resolveViewport(page) {
    const viewport = page.viewportSize();
    if (viewport?.width && viewport?.height) {
        return viewport;
    }
    try {
        const liveViewport = await page.evaluate(() => ({
            width: window.innerWidth || document.documentElement.clientWidth || 0,
            height: window.innerHeight || document.documentElement.clientHeight || 0,
        }));
        if (Number(liveViewport?.width) > 0 && Number(liveViewport?.height) > 0) {
            return {
                width: Number(liveViewport.width),
                height: Number(liveViewport.height),
            };
        }
    }
    catch {
        // Если вкладка еще не готова к evaluate, ниже используем безопасный fallback.
    }
    return { width: 1280, height: 800 };
}
async function humanMove(page, targetX, targetY, options) {
    const prof = options?.profile ?? generateHumanProfile();
    let currentPos = options?.currentPos;
    if (!currentPos) {
        const vp = await _resolveViewport(page);
        currentPos = [vp.width * rand(0.3, 0.7), vp.height * rand(0.3, 0.7)];
    }
    const jitter = rand(1, 4) * prof.jitterFactor;
    const dest = [
        targetX + rand(-jitter, jitter),
        targetY + rand(-jitter, jitter),
    ];
    const steps = randInt(prof.bezierStepsMin, prof.bezierStepsMax);
    const points = _bezierPath(currentPos, dest, steps, prof);
    const noiseSeed = rand(0, 100);
    const n = points.length;
    for (let i = 0; i < n; i++) {
        const [x, y] = points[i];
        await page.mouse.move(x, y);
        const t = i / Math.max(n - 1, 1);
        const ease = 4 * t * (1 - t);
        const baseDelay = rand(0.005, 0.025) * (1.5 - ease) * prof.speedFactor;
        const delay = _perlinDelay(baseDelay * 0.5, baseDelay * 1.5, t, noiseSeed);
        await sleep(delay * 1000);
    }
    // Легкий перелет курсора и возврат делают движение менее механическим.
    if (Math.random() < prof.overshootChance) {
        const overX = dest[0] + rand(-8, 8) * prof.jitterFactor;
        const overY = dest[1] + rand(-4, 4) * prof.jitterFactor;
        await page.mouse.move(overX, overY);
        await sleep(rand(0.04, 0.10) * prof.pauseFactor * 1000);
        await page.mouse.move(dest[0], dest[1]);
        await sleep(rand(0.03, 0.07) * prof.pauseFactor * 1000);
    }
}
async function humanClick(page, element, options) {
    const prof = options?.profile ?? generateHumanProfile();
    const doubleCheckPause = options?.doubleCheckPause ?? true;
    await element.scrollIntoViewIfNeeded();
    await sleep(rand(0.2, 0.5) * prof.pauseFactor * 1000);
    const box = await element.boundingBox();
    if (!box) {
        await element.click();
        return;
    }
    const clickX = box.x + box.width * rand(0.25, 0.75);
    const clickY = box.y + box.height * rand(0.25, 0.75);
    await humanMove(page, clickX, clickY, { profile: prof });
    // Пауза «наведения»
    await sleep(rand(0.08, 0.25) * prof.pauseFactor * 1000);
    // Пауза «читаю и принимаю решение»
    if (doubleCheckPause) {
        await sleep(rand(0.3, 1.2) * prof.pauseFactor * 1000);
    }
    // Случайная idle-пауза «отвлёкся»
    if (Math.random() < prof.idleChance) {
        await sleep(rand(prof.idleDurationMin, prof.idleDurationMax) * 1000);
    }
    // Нажатие разделяем на down/up с паузой, чтобы не выглядеть мгновенным кликом.
    await page.mouse.down();
    await sleep(rand(0.06, 0.18) * prof.pauseFactor * 1000);
    await page.mouse.up();
    // Дрейф после клика
    await sleep(rand(0.05, 0.15) * prof.pauseFactor * 1000);
    const driftX = clickX + rand(-6, 6) * prof.jitterFactor;
    const driftY = clickY + rand(-3, 3) * prof.jitterFactor;
    await page.mouse.move(driftX, driftY);
}
async function humanScrollToFind(page, selector, options) {
    const prof = options?.profile ?? generateHumanProfile();
    const maxSteps = options?.maxSteps ?? 30;
    const vp = await _resolveViewport(page);
    const anchor = await _resolveScrollAnchor(page);
    let scrollX, scrollY;
    if (!anchor) {
        scrollX = vp.width * rand(0.35, 0.65);
        scrollY = vp.height * rand(0.40, 0.60);
    }
    else {
        scrollX = Math.min(Math.max(anchor[0], 24), Math.max(vp.width - 24, 24));
        scrollY = Math.min(Math.max(anchor[1], 24), Math.max(vp.height - 24, 24));
    }
    await page.mouse.move(scrollX, scrollY);
    await sleep(rand(0.1, 0.3) * prof.pauseFactor * 1000);
    for (let stepI = 0; stepI < maxSteps; stepI++) {
        const el = await page.$(selector);
        if (el)
            return el;
        const px = options?.stepPx ?? randInt(250, 550);
        await page.mouse.wheel(0, px);
        if (Math.random() < prof.idleChance) {
            await sleep(rand(prof.idleDurationMin, prof.idleDurationMax) * 1000);
        }
        else if (Math.random() < 0.15) {
            await sleep(rand(0.8, 2.0) * prof.pauseFactor * 1000);
        }
        else {
            const t = stepI / Math.max(maxSteps - 1, 1);
            const delay = _perlinDelay(0.25, 0.65, t, scrollX) * prof.pauseFactor;
            await sleep(delay * 1000);
        }
        if (Math.random() < 0.30) {
            const jx = rand(-20, 20) * prof.jitterFactor;
            const jy = rand(-10, 10) * prof.jitterFactor;
            await page.mouse.move(scrollX + jx, scrollY + jy);
        }
    }
    return null;
}
async function humanWheelScroll(page, deltaY, options) {
    const prof = options?.profile ?? generateHumanProfile();
    const moveBefore = options?.moveBefore ?? true;
    const settleRange = options?.settleRange ?? [0.25, 0.65];
    const driftXRange = options?.driftXRange ?? [-20, 20];
    const driftYRange = options?.driftYRange ?? [-10, 10];
    let anchor = options?.anchor;
    if (!anchor) {
        const vp = await _resolveViewport(page);
        anchor = [vp.width * rand(0.35, 0.65), vp.height * rand(0.40, 0.60)];
    }
    if (moveBefore) {
        await humanMove(page, anchor[0], anchor[1], { profile: prof });
        await sleep(rand(0.04, 0.12) * prof.pauseFactor * 1000);
    }
    await page.mouse.wheel(0, deltaY);
    if (Math.random() < prof.idleChance) {
        await sleep(rand(prof.idleDurationMin, prof.idleDurationMax) * 1000);
    }
    const driftX = anchor[0] + rand(...driftXRange) * prof.jitterFactor;
    const driftY = anchor[1] + rand(...driftYRange) * prof.jitterFactor;
    await page.mouse.move(driftX, driftY);
    await sleep(rand(...settleRange) * prof.pauseFactor * 1000);
    return [driftX, driftY];
}
function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
function rand(min, max) {
    return Math.random() * (max - min) + min;
}
function randInt(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
}
//# sourceMappingURL=humanizer.js.map