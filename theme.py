from nicegui import ui

def apply_theme():
    # Enable Tailwind glassmorphism and general layout styling
    ui.add_head_html("""
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
        <style>
            .q-tab[name="Apache Superset"] .q-tab__icon img,
            .q-tab[name="Telemetry"] .q-tab__icon img {
                width: 30px !important;
                height: 30px !important;
            }
            
            /* --- TYPOGRAPHY & SMOOTH BG GRADIENTS --- */
            body, html {
                margin: 0 !important;
                padding: 0 !important;
                height: 100vh !important;
                width: 100vw !important;
                overflow: hidden !important;
                font-family: 'Outfit', -apple-system, BlinkMacSystemFont, sans-serif !important;
                background-color: #f8fafc;
                background-image: 
                    radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.05) 0px, transparent 50%),
                    radial-gradient(at 50% 0%, rgba(139, 92, 246, 0.04) 0px, transparent 50%),
                    radial-gradient(at 100% 0%, rgba(236, 72, 153, 0.04) 0px, transparent 50%);
                background-attachment: fixed;
                transition: background-color 0.3s ease;
            }
            .body--dark {
                background-color: #030712 !important;
                background-image: 
                    radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.12) 0px, transparent 50%),
                    radial-gradient(at 50% 0%, rgba(139, 92, 246, 0.08) 0px, transparent 50%),
                    radial-gradient(at 100% 0%, rgba(236, 72, 153, 0.08) 0px, transparent 50%) !important;
                background-attachment: fixed;
            }
            
            /* --- GLASSMORPHIC COMPONENT CARD CLASSES --- */
            .glass-card {
                background: rgba(255, 255, 255, 0.45) !important;
                backdrop-filter: blur(16px) saturate(120%) !important;
                -webkit-backdrop-filter: blur(16px) saturate(120%) !important;
                border: 1px solid rgba(255, 255, 255, 0.5) !important;
                border-radius: 12px;
                box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.03) !important;
                transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
            }
            .body--dark .glass-card {
                background: rgba(17, 24, 39, 0.45) !important;
                border: 1px solid rgba(255, 255, 255, 0.06) !important;
                box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.24) !important;
            }
            .glass-card:hover {
                box-shadow: 0 12px 40px 0 rgba(31, 38, 135, 0.06) !important;
                border-color: rgba(99, 102, 241, 0.3) !important;
            }
            .body--dark .glass-card:hover {
                box-shadow: 0 12px 40px 0 rgba(0, 0, 0, 0.35) !important;
                border-color: rgba(129, 140, 248, 0.15) !important;
            }

            .custom-header {
                background: rgba(255, 255, 255, 0.6) !important;
                backdrop-filter: blur(20px) !important;
                -webkit-backdrop-filter: blur(20px) !important;
                border-bottom: 1px solid rgba(226, 232, 240, 0.8) !important;
            }
            .body--dark .custom-header {
                background: rgba(15, 23, 42, 0.65) !important;
                border-bottom: 1px solid rgba(255, 255, 255, 0.06) !important;
            }
            
            .sidebar-card {
                background: rgba(255, 255, 255, 0.3) !important;
                backdrop-filter: blur(12px) !important;
                border-right: 1px solid rgba(226, 232, 240, 0.8) !important;
            }
            .body--dark .sidebar-card {
                background: rgba(15, 23, 42, 0.3) !important;
                border-right: 1px solid rgba(255, 255, 255, 0.06) !important;
            }
            
            .dark-bg-panel {
                background-color: rgba(255, 255, 255, 0.5) !important;
                backdrop-filter: blur(8px) !important;
                border: 1px solid rgba(226, 232, 240, 0.7) !important;
                transition: all 0.3s ease;
            }
            .body--dark .dark-bg-panel {
                background-color: rgba(17, 24, 39, 0.5) !important;
                border: 1px solid rgba(255, 255, 255, 0.06) !important;
            }
            .dark-bg-flat {
                background-color: rgba(248, 250, 252, 0.3) !important;
                transition: all 0.3s ease;
            }
            .body--dark .dark-bg-flat {
                background-color: rgba(15, 23, 42, 0.3) !important;
            }

            /* --- CUSTOM SMOOTH SCROLLBARS --- */
            ::-webkit-scrollbar {
                width: 6px;
                height: 6px;
            }
            ::-webkit-scrollbar-track {
                background: transparent;
            }
            ::-webkit-scrollbar-thumb {
                background: rgba(156, 163, 175, 0.25);
                border-radius: 10px;
                transition: background 0.3s ease;
            }
            .body--dark ::-webkit-scrollbar-thumb {
                background: rgba(156, 163, 175, 0.12);
            }
            ::-webkit-scrollbar-thumb:hover {
                background: rgba(99, 102, 241, 0.4);
            }

            /* --- Q-TREE CUSTOM STYLING --- */
            .body--dark .q-tree .q-icon {
                color: #818cf8 !important;
            }
            .body--dark .q-tree,
            .body--dark .q-tree__node-header,
            .body--dark .q-tree__node-header-content,
            .body--dark .q-tree__node-label,
            .body--dark .q-tree__label,
            .body--dark .q-tree div,
            .body--dark .q-tree span {
                color: #e2e8f0 !important;
            }
            
            /* --- CodeMirror Light/Dark Theme Sync --- */
            .cm-editor {
                background-color: rgba(255, 255, 255, 0.5) !important;
                color: #0f172a !important;
                border: 1px solid rgba(203, 213, 225, 0.7) !important;
                border-radius: 8px;
                backdrop-filter: blur(8px) !important;
            }
            .cm-editor .cm-scroller {
                background-color: transparent !important;
            }
            .cm-editor .cm-content {
                color: #0f172a !important;
                font-family: 'JetBrains Mono', monospace !important;
            }
            .cm-editor .cm-gutters {
                background-color: rgba(241, 245, 249, 0.6) !important;
                color: #64748b !important;
                border-right: 1px solid rgba(203, 213, 225, 0.6) !important;
            }
            .body--dark .cm-editor {
                background-color: rgba(15, 23, 42, 0.5) !important;
                color: #f8fafc !important;
                border: 1px solid rgba(255, 255, 255, 0.06) !important;
            }
            .body--dark .cm-editor .cm-content {
                color: #f8fafc !important;
            }
            .body--dark .cm-editor .cm-gutters {
                background-color: rgba(30, 41, 59, 0.5) !important;
                color: #94a3b8 !important;
                border-right: 1px solid rgba(255, 255, 255, 0.04) !important;
            }

             /* --- Results Layout and Data Grid Scroll --- */
             .q-tab-panels {
                 background-color: transparent !important;
                 height: 100% !important;
             }
             .q-panel.scroll {
                 height: 100% !important;
                 overflow: hidden !important;
             }
             .q-tab-panel {
                 padding: 0 !important;
             }

             /* --- Q-Table Scroll & Fit --- */
             .q-table__container {
                 height: 100% !important;
                 display: flex !important;
                 flex-direction: column !important;
                 flex-wrap: nowrap !important;
                 border-radius: 8px !important;
                 background: transparent !important;
             }
             .q-table__middle {
                 flex-grow: 1 !important;
                 height: 100% !important;
                 max-height: 100% !important;
             }

             /* --- Sticky Table Header --- */
             .q-table thead tr th {
                 position: sticky !important;
                 z-index: 2 !important;
                 top: 0 !important;
                 background-color: rgba(255, 255, 255, 0.8) !important;
                 backdrop-filter: blur(10px) !important;
             }
             .body--dark .q-table thead tr th {
                 background-color: rgba(15, 23, 42, 0.8) !important;
                 color: #cbd5e1 !important;
             }

             /* --- Super Dense Inputs for API Docs --- */
             .super-dense-input .q-field__control,
             .super-dense-input .q-field__marginal {
                 height: 26px !important;
                 min-height: 26px !important;
             }
             .super-dense-input .q-field__native,
             .super-dense-input .q-field__input {
                 font-size: 10px !important;
                 height: 26px !important;
                 min-height: 26px !important;
                 padding: 0 4px !important;
             }
        </style>
        <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
        <script>
            mermaid.initialize({ startOnLoad: false, securityLevel: 'loose', theme: 'dark' });
        </script>
        <script>
            document.addEventListener('keydown', function(e) {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    const target = e.target;
                    if (target && (target.tagName === 'TEXTAREA' || target.closest('.cm-editor') || target.closest('.CodeMirror'))) {
                        e.preventDefault();
                    }
                }
            }, true);
        </script>
    """)
