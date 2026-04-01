# HedgePoint MX

Consultoría en Gestión de Riesgos Financieros para PyMEs mexicanas.

## Stack
- **Backend:** Python + FastAPI
- **Modelos cuantitativos:** scipy, numpy, scikit-learn, XGBoost
- **LLM local:** Ollama + Llama 3.1 8B / Qwen 2.5 7B
- **LLM comercial:** Claude API (Sonnet)
- **Base de datos:** PostgreSQL con pgcrypto
- **Canal de comunicación:** WhatsApp Business API

## Estructura del Proyecto

```
HedgePointMx/
├── agents/                 # Agentes de IA
│   ├── market-monitor/     # Monitoreo de mercado y alertas
│   ├── onboarding/         # Diagnóstico automático de prospectos
│   ├── reports/            # Reportes personalizados
│   ├── simulator/          # Simulador de escenarios
│   ├── support/            # Atención y seguimiento
│   └── timing/             # Análisis de timing (ML)
├── core/                   # Lógica compartida
│   ├── models/pricing.py   # Black-Scholes, forwards, Monte Carlo
│   ├── data/market_data.py # Conexión a Banxico y Alpha Vantage
│   └── security/           # Anonimización y encriptación
├── config/                 # Archivos de configuración
├── docs/                   # Documentación
├── templates/              # Templates de reportes y PDFs
└── tests/                  # Tests automatizados
```

## Setup

```bash
python -m venv venv
.\venv\Scripts\activate       # Windows
pip install -r requirements.txt
copy .env.example .env        # Configurar API keys
```
