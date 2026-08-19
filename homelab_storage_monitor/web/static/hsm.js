/* Shared dashboard behavior: local timestamps, auto-refresh, chart helpers. */

(function () {
    'use strict';

    // ---------------------------------------------------------------------
    // Timestamps: server renders UTC fallbacks in .ts spans; convert to the
    // viewer's local time. .ts-rel spans show a live relative time instead.
    // ---------------------------------------------------------------------

    function pad(n) { return String(n).padStart(2, '0'); }

    function formatLocal(date) {
        return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
            + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes());
    }

    function formatRelative(date) {
        var diff = (Date.now() - date.getTime()) / 1000;
        var future = diff < 0;
        diff = Math.abs(diff);

        var text;
        if (diff < 60) text = 'moments';
        else if (diff < 3600) text = Math.round(diff / 60) + ' min';
        else if (diff < 86400) text = Math.round(diff / 3600 * 10) / 10 + ' h';
        else text = Math.round(diff / 86400 * 10) / 10 + ' d';

        if (diff < 60) return future ? 'shortly' : 'just now';
        return future ? 'in ' + text : text + ' ago';
    }

    function renderTimestamps() {
        document.querySelectorAll('.ts').forEach(function (el) {
            var date = new Date(el.dataset.ts);
            if (isNaN(date.getTime())) return;
            el.title = date.toLocaleString();
            el.textContent = el.classList.contains('ts-rel')
                ? formatRelative(date)
                : formatLocal(date);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        renderTimestamps();
        setInterval(renderTimestamps, 30000);
    });

    // ---------------------------------------------------------------------
    // Auto-refresh: poll for a new check run and reload when one appears.
    // ---------------------------------------------------------------------

    window.hsmAutoRefresh = function (currentRunId) {
        setInterval(function () {
            fetch('/api/runs?limit=1', { credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (runs) {
                    if (!runs) return;
                    var latestId = runs.length ? runs[0].id : null;
                    if (latestId !== null && latestId !== currentRunId) {
                        window.location.reload();
                    }
                })
                .catch(function () { /* transient network errors are fine */ });
        }, 60000);
    };

    // ---------------------------------------------------------------------
    // Charts
    // ---------------------------------------------------------------------

    function timeTickLabel(iso) {
        var date = new Date(iso);
        if (isNaN(date.getTime())) return iso;
        var now = new Date();
        if (now - date < 86400000 && date.getDate() === now.getDate()) {
            return pad(date.getHours()) + ':' + pad(date.getMinutes());
        }
        return (date.getMonth() + 1) + '/' + date.getDate();
    }

    /* Full-size line chart with a real time axis and optional threshold lines.
       series: {ts: [...iso], values: [...]}
       opts: {color, yMin, yMax, yLabel, thresholds: [{value, color, label}]} */
    window.hsmTimeChart = function (canvasId, series, opts) {
        var ctx = document.getElementById(canvasId);
        if (!ctx || typeof Chart === 'undefined' || !series.ts.length) return;
        opts = opts || {};

        var datasets = [{
            label: opts.yLabel || '',
            data: series.values,
            borderColor: opts.color || '#007bff',
            backgroundColor: (opts.color || '#007bff') + '1a',
            fill: true,
            tension: 0.1,
            pointRadius: 0,
            pointHitRadius: 8,
            borderWidth: 2
        }];

        (opts.thresholds || []).forEach(function (t) {
            datasets.push({
                label: t.label,
                data: series.ts.map(function () { return t.value; }),
                borderColor: t.color,
                borderDash: [6, 4],
                borderWidth: 1,
                pointRadius: 0,
                pointHitRadius: 0,
                fill: false
            });
        });

        new Chart(ctx, {
            type: 'line',
            data: { labels: series.ts, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                scales: {
                    y: {
                        min: opts.yMin,
                        max: opts.yMax,
                        title: opts.yLabel ? { display: true, text: opts.yLabel } : undefined
                    },
                    x: {
                        ticks: {
                            maxTicksLimit: 8,
                            maxRotation: 0,
                            callback: function (value, index) {
                                return timeTickLabel(this.getLabelForValue(index));
                            }
                        }
                    }
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        filter: function (item) { return item.datasetIndex === 0; },
                        callbacks: {
                            title: function (items) {
                                if (!items.length) return '';
                                var date = new Date(items[0].label);
                                return isNaN(date.getTime()) ? items[0].label : date.toLocaleString();
                            }
                        }
                    }
                }
            }
        });
    };

    /* Compact sparkline for attribute mini-cards. */
    window.hsmSparkline = function (canvasId, series, color) {
        var ctx = document.getElementById(canvasId);
        if (!ctx || typeof Chart === 'undefined' || !series.ts.length) return;

        new Chart(ctx, {
            type: 'line',
            data: {
                labels: series.ts,
                datasets: [{
                    data: series.values,
                    borderColor: color || '#6c757d',
                    borderWidth: 1.5,
                    pointRadius: 0,
                    pointHitRadius: 6,
                    tension: 0.1,
                    fill: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: { y: { display: false }, x: { display: false } },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            title: function (items) {
                                if (!items.length) return '';
                                var date = new Date(items[0].label);
                                return isNaN(date.getTime()) ? items[0].label : date.toLocaleString();
                            }
                        }
                    }
                }
            }
        });
    };
})();
