// icons.jsx — Lucide-style thin-stroke inline icons. window.Icon({name, size, ...})
(function () {
  const P = {
    scan:      's M3 7V5a2 2 0 0 1 2-2h2 M17 3h2a2 2 0 0 1 2 2v2 M21 17v2a2 2 0 0 1-2 2h-2 M7 21H5a2 2 0 0 1-2-2v-2 M7 12h10',
    search:    's M11 11m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0 M21 21l-4.3-4.3',
    chevR:     's M9 6l6 6-6 6',
    chevL:     's M15 6l-6 6 6 6',
    chevD:     's M6 9l6 6 6-6',
    arrowU:    's M12 19V5 M5 12l7-7 7 7',
    arrowD:    's M12 5v14 M19 12l-7 7-7-7',
    x:         's M18 6L6 18 M6 6l12 12',
    dashboard: 's M4 4h7v7H4z M13 4h7v5h-7z M13 12h7v8h-7z M4 14h7v6H4z',
    ads:       's M4 6h16 M4 12h16 M4 18h10',
    drafts:    's M5 4h10l4 4v12a0 0 0 0 1 0 0H5z M14 4v5h5 M9 13h6 M9 17h4',
    offers:    's M3 7l1.5-3h7L20 12.5a2 2 0 0 1 0 2.8l-5.7 5.7a2 2 0 0 1-2.8 0L3 12.5V7z M8 8h.01',
    history:   's M3 12a9 9 0 1 0 3-6.7L3 8 M3 4v4h4 M12 8v4l3 2',
    settings:  's M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M19.4 13.5a1 1 0 0 0 .2 1.1l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1 1 0 0 0-1.7.7v.2a2 2 0 1 1-4 0v-.1a1 1 0 0 0-1.7-.7l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1 1 0 0 0-.7-1.7H4a2 2 0 1 1 0-4h.1a1 1 0 0 0 .7-1.7l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1 1 0 0 0 1.7-.7V4a2 2 0 1 1 4 0v.1a1 1 0 0 0 1.7.7l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1 1 0 0 0-.2 1.1z',
    activity:  's M22 12h-4l-3 9L9 3l-3 9H2',
    alert:     's M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z M12 9v4 M12 17h.01',
    stop:      's M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9z M12 8v4 M12 16h.01',
    check:     's M20 6L9 17l-5-5',
    more:      's M12 12h.01 M19 12h.01 M5 12h.01',
    rotate:    's M3 12a9 9 0 1 0 3-6.7L3 8 M3 3v5h5',
    refresh:   's M3 12a9 9 0 0 1 9-9 9.7 9.7 0 0 1 6.7 2.7L21 8 M21 3v5h-5 M21 12a9 9 0 0 1-9 9 9.7 9.7 0 0 1-6.7-2.7L3 16 M3 21v-5h5',
    snooze:    's M12 22a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 8v4l3 2 M9 2h6',
    filter:    's M3 4h18l-7 8v6l-4 2v-8z',
    sliders:   's M4 21v-7 M4 10V3 M12 21v-9 M12 8V3 M20 21v-5 M20 12V3 M1 14h6 M9 8h6 M17 16h6',
    panel:     's M3 4h18v16H3z M9 4v16',
    plus:      's M12 5v14 M5 12h14',
    command:   's M18 3a3 3 0 0 0-3 3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3z',
    pulse:     's M22 12h-4l-3 9L9 3l-3 9H2',
    bell:      's M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9 M13.7 21a2 2 0 0 1-3.4 0',
    clock:     's M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z M12 6v6l4 2',
    inbox:     's M22 12h-6l-2 3h-4l-2-3H2 M5.5 5.1L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.7 4H7.3a2 2 0 0 0-1.8 1.1z',
    zap:       'f M13 2L3 14h7l-1 8 10-12h-7z',
    user:      's M20 21a8 8 0 1 0-16 0 M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
    external:  's M15 3h6v6 M10 14L21 3 M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5',
    play:      'f M6 4l14 8-14 8z',
    pause:     's M9 4v16 M15 4v16',
    power:     's M12 3v9 M6.4 7.4a8 8 0 1 0 11.2 0',
    dot:       's M12 12h.01',
  };

  function Icon({ name, size = 16, stroke = 1.5, color = 'currentColor', style }) {
    const spec = P[name];
    if (!spec) return null;
    const filled = spec[0] === 'f';
    const d = spec.slice(2);
    const paths = d.split(' M').map((seg, i) => (i === 0 ? seg : 'M' + seg));
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
        style={{ display: 'block', flex: 'none', ...style }} aria-hidden="true">
        {paths.map((pd, i) => (
          <path key={i} d={pd}
            fill={filled ? color : 'none'}
            stroke={filled ? 'none' : color}
            strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" />
        ))}
      </svg>
    );
  }
  window.Icon = Icon;
})();
