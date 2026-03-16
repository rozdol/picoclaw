Set up AgentMail for my OpenClaw agent.

The AgentMail skill docs and reference are at:
https://clawhub.ai/adboio/agentmail

Make sure to:
1. The AgentMail skill is already installed via clawhub (if not installed, please do so)
2. Configure the AGENTMAIL_API_KEY in my openclaw.json.  My key is:  am_us_8d767dc224fb5eae7f4a824fb054b6d216bf6f515250c9217602617c81104e5b
   under skills.entries.agentmail
3. My agent's inbox is: rozdol
4. Install the Python SDK (pip install agentmail python-dotenv)
5. Test sending and receiving an email