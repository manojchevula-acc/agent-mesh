"""Framework adapters. Import the specific module so optional dependencies stay optional:

* ``gernas.adapters.asgi``             — GernasASGIMiddleware (no dependency)
* ``gernas.adapters.httpx``            — httpx event hooks (no dependency)
* ``gernas.adapters.agent_framework``  — Microsoft Agent Framework middlewares
* ``gernas.adapters.a2a``              — A2A client interceptor (a2a-sdk)
* ``gernas.adapters.langchain``        — LangChain callback handler (langchain-core)
"""
