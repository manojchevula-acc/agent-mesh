import os
import sys

# ANSI Escape Sequences for harmonious terminal styling
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# Harmonious HSL-like terminal colors
COLOR_USER = "\033[38;5;15m"       # Bold white
COLOR_COORD = "\033[38;5;75m"      # Sky blue
COLOR_POLICY = "\033[38;5;141m"    # Medium purple
COLOR_COMPLIANCE = "\033[38;5;214m" # Warm orange
COLOR_APPROVAL = "\033[38;5;51m"   # Bright cyan
COLOR_ACTION = "\033[38;5;78m"     # Safe emerald green
COLOR_SYSTEM = "\033[38;5;244m"     # Neutral gray

class AgentLogger:
    """Beautiful, colored console logging utility for multi-agent terminal interactions."""
    RESET = RESET
    BOLD = BOLD
    DIM = DIM
    COLOR_USER = COLOR_USER
    COLOR_COORD = COLOR_COORD
    COLOR_POLICY = COLOR_POLICY
    COLOR_COMPLIANCE = COLOR_COMPLIANCE
    COLOR_APPROVAL = COLOR_APPROVAL
    COLOR_ACTION = COLOR_ACTION
    COLOR_SYSTEM = COLOR_SYSTEM
    
    @staticmethod
    def print_agent_header(agent_name: str, action: str):
        color = COLOR_SYSTEM
        name_lower = agent_name.lower()
        if "coord" in name_lower:
            color = COLOR_COORD
        elif "policy" in name_lower:
            color = COLOR_POLICY
        elif "compliance" in name_lower:
            color = COLOR_COMPLIANCE
        elif "approval" in name_lower:
            color = COLOR_APPROVAL
        elif "action" in name_lower:
            color = COLOR_ACTION
        
        sys.stdout.write(f"\n{color}{BOLD}[* {agent_name}]{RESET} {color}{action}{RESET}\n")
        sys.stdout.flush()

    @staticmethod
    def print_agent_response(agent_name: str, text: str):
        color = COLOR_SYSTEM
        name_lower = agent_name.lower()
        if "coord" in name_lower:
            color = COLOR_COORD
        elif "policy" in name_lower:
            color = COLOR_POLICY
        elif "compliance" in name_lower:
            color = COLOR_COMPLIANCE
        elif "approval" in name_lower:
            color = COLOR_APPROVAL
        elif "action" in name_lower:
            color = COLOR_ACTION
            
        # Indent response for readability
        lines = text.strip().split("\n")
        for line in lines:
            sys.stdout.write(f"  {color}|{RESET}  {line}\n")
        sys.stdout.flush()

    @staticmethod
    def print_user_prompt(text: str):
        sys.stdout.write(f"\n{COLOR_USER}{BOLD}>> [User Request]{RESET} {COLOR_USER}{text}{RESET}\n")
        sys.stdout.flush()

    @staticmethod
    def print_system_info(text: str):
        sys.stdout.write(f"{COLOR_SYSTEM}{DIM}[System Info] {text}{RESET}\n")
        sys.stdout.flush()

    @staticmethod
    def print_error(text: str):
        sys.stdout.write(f"\n\033[38;5;196m{BOLD}[Error]{RESET} \033[38;5;196m{text}{RESET}\n")
        sys.stdout.flush()
        
    @staticmethod
    def print_success(text: str):
        sys.stdout.write(f"\n{COLOR_ACTION}{BOLD}[Success]{RESET} {COLOR_ACTION}{text}{RESET}\n")
        sys.stdout.flush()
