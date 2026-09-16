(() => {
    const rgba = value => (value.match(/[\d.]+/g) || []).map(Number);
    const blend = (front, back) => front.slice(0, 3).map((v, i) => v * (front[3] ?? 1) + back[i] * (1 - (front[3] ?? 1)));
    const background = element => {
        if (!element) return [255, 255, 255];
        return blend(rgba(getComputedStyle(element).backgroundColor), background(element.parentElement));
    };
    const luminance = rgb => rgb.slice(0, 3).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
    const ratio = (a, b) => {
        const values = [luminance(a), luminance(b)].sort((x, y) => x - y);
        return (values[1] + .05) / (values[0] + .05);
    };
    const failures = [];
    const requestedColorExceptions = [];
    let minimum = 99, checked = 0;
    const check = (element, foreground, bg, threshold, kind) => {
        const value = ratio(foreground, bg);
        if (kind === 'text') minimum = Math.min(minimum, value);
        checked++;
        if (value < threshold) failures.push({kind, tag: element.tagName, class: element.className, text: element.textContent.trim().slice(0, 50), ratio: +value.toFixed(2)});
    };
    for (const element of document.body.querySelectorAll('*')) {
        if (element.namespaceURI !== 'http://www.w3.org/1999/xhtml' || !element.getClientRects().length || element.closest('#save-toast')) continue;
        const style = getComputedStyle(element);
        if (style.visibility === 'hidden') continue;
        const bg = background(element);
        const directText = [...element.childNodes].some(node => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
        // 주차권 뱃지는 사용자가 지정한 배경 #EA4B46 + 흰 글자를 그대로 검증한다.
        const requestedBadge = element.matches('.parking-count')
            && style.backgroundColor === 'rgb(234, 75, 70)' && style.color === 'rgb(255, 255, 255)';
        if (requestedBadge) requestedColorExceptions.push({class: element.className, ratio: +ratio(rgba(style.color), bg).toFixed(2)});
        else if (directText || element.matches('input:not([type=checkbox]),select')) check(element, rgba(style.color), bg, 4.5, 'text');
        if (element.matches('input[placeholder]')) check(element, rgba(getComputedStyle(element, '::placeholder').color), bg, 4.5, 'placeholder');
        if (element.matches('input:not([type=checkbox]),select,.btn,.theme-button,.filter-btn')) check(element, rgba(style.borderTopColor), background(element.parentElement), 3, 'control');
    }
    return {checked, minimumText: +minimum.toFixed(2), failures, requestedColorExceptions};
})()
