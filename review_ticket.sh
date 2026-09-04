#!/bin/bash
#
# Shell script to review HITL queue tickets and process approvals/rejections
# Usage:
#   ./review_ticket.sh approve <ticket_filename> [--reviewer NAME] [--notes TEXT] [--live]
#   ./review_ticket.sh reject <ticket_filename> [--reviewer NAME] [--notes TEXT] [--live]
#   ./review_ticket.sh check-merged [--live]
#
# Examples:
#   ./review_ticket.sh approve hitl_queue/1725145800_image-alt_fix.json --live
#   ./review_ticket.sh reject hitl_queue/1725145800_image-alt_fix.json --notes "Doesn't match codebase style"
#   ./review_ticket.sh check-merged --live
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

usage() {
    echo "Usage:"
    echo "  $0 approve <ticket_file> [--reviewer NAME] [--notes TEXT] [--live]"
    echo "  $0 reject <ticket_file> [--reviewer NAME] [--notes TEXT] [--live]"
    echo "  $0 check-merged [--live]"
    echo ""
    echo "Examples:"
    echo "  $0 approve hitl_queue/1725145800_image-alt_fix.json --live"
    echo "  $0 reject hitl_queue/1725145800_image-alt_fix.json --notes 'Needs revision'"
    echo "  $0 check-merged --live"
    exit 1
}

if [[ $# -lt 1 ]]; then
    usage
fi

DECISION="$1"
shift

case "$DECISION" in
    approve|reject)
        if [[ $# -lt 1 ]]; then
            echo -e "${RED}❌ Error: ticket file path required${NC}"
            usage
        fi
        
        TICKET_FILE="$1"
        shift
        
        # Parse optional arguments
        REVIEWER="cli"
        NOTES=""
        LIVE_FLAG="--no-live"
        
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --reviewer)
                    REVIEWER="$2"
                    shift 2
                    ;;
                --notes)
                    NOTES="$2"
                    shift 2
                    ;;
                --live)
                    LIVE_FLAG="--live"
                    shift
                    ;;
                *)
                    echo -e "${RED}❌ Unknown option: $1${NC}"
                    usage
                    ;;
            esac
        done
        
        echo -e "${BLUE}📋 Processing $DECISION for: $TICKET_FILE${NC}"
        echo -e "   Reviewer: $REVIEWER"
        if [[ -n "$NOTES" ]]; then
            echo -e "   Feedback: $NOTES"
        fi
        echo -e "   Mode: $LIVE_FLAG"
        echo ""
        
        # Build the CLI command with proper flag order
        CMD="python -m a11y_fixer.cli review \"$TICKET_FILE\""
        
        if [[ "$DECISION" == "approve" ]]; then
            CMD="$CMD --approve"
        else
            CMD="$CMD --reject"
        fi
        
        if [[ -n "$NOTES" ]]; then
            CMD="$CMD --notes \"$NOTES\""
        fi
        
        CMD="$CMD --reviewer \"$REVIEWER\" $LIVE_FLAG"
        
        echo -e "${YELLOW}▶ Running: $CMD${NC}"
        echo ""
        
        eval "$CMD"
        
        if [[ $? -eq 0 ]]; then
            echo ""
            if [[ "$DECISION" == "approve" ]]; then
                echo -e "${GREEN}✅ Ticket approved and processed${NC}"
                echo -e "   PR created and will be auto-merged if tests pass"
                echo -e "   Lesson will be stored in wiki/lessons/"
            else
                echo -e "${GREEN}✅ Ticket rejected and moved to revision queue${NC}"
                echo -e "   Agent will receive feedback for improvement"
            fi
        else
            echo ""
            echo -e "${RED}❌ Review process failed${NC}"
            exit 1
        fi
        ;;
        
    check-merged)
        LIVE_FLAG="--no-live"
        
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --live)
                    LIVE_FLAG="--live"
                    shift
                    ;;
                *)
                    echo -e "${RED}❌ Unknown option: $1${NC}"
                    usage
                    ;;
            esac
        done
        
        echo -e "${BLUE}🔄 Checking GitHub for merged PRs${NC}"
        echo -e "   Mode: $LIVE_FLAG"
        echo ""
        
        CMD="python -m a11y_fixer.cli queue-sync --check-merged $LIVE_FLAG"
        
        echo -e "${YELLOW}▶ Running: $CMD${NC}"
        echo ""
        
        eval "$CMD"
        ;;
        
    *)
        echo -e "${RED}❌ Unknown decision: $DECISION${NC}"
        usage
        ;;
esac
