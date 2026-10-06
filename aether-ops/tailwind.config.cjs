module.exports = {
            content: ['./public/*.html', './app/**/*.js'],
            theme: {
                extend: {
                    colors: {
                        navy: {
                            50: '#eff6ff',
                            100: '#dbeafe',
                            200: '#bfdbfe',
                            700: '#1d4ed8',
                            800: '#1e40af',
                            900: '#1e3a8a',
                            950: '#0f2b46'
                        },
                        ops: {
                            terminal: '#091322',
                            border: '#dbeafe',
                            panel: '#f8fafc',
                            card: '#ffffff'
                        }
                    },
                    fontFamily: {
                        mono: ['JetBrains Mono', 'Menlo', 'Monaco', 'Courier New', 'monospace'],
                        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif']
                    },
                    boxShadow: {
                        card: '0 2px 8px -1px rgba(30, 58, 138, 0.05), 0 1px 3px -1px rgba(30, 58, 138, 0.03)',
                        elevation: '0 10px 25px -3px rgba(30, 58, 138, 0.08), 0 4px 6px -2px rgba(30, 58, 138, 0.04)'
                    }
                }
            }
        };
