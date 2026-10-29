"""Optional MCP v1 stdio adapter. Requires the official `mcp` SDK."""
from interview_practice.agent import QuoteArgs, SearchArgs, estimate_quote, search_knowledge


def main():
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("Agency practice tools")

    @server.tool()
    def search(query: str) -> dict:
        """Search agency service facts."""
        args = SearchArgs(query=query)
        return search_knowledge(args.query)

    @server.tool()
    def quote(hours: int, hourly_rate: int) -> dict:
        """Calculate an estimate in INR; does not create an invoice."""
        args = QuoteArgs(hours=hours, hourly_rate=hourly_rate)
        return estimate_quote(args.hours, args.hourly_rate)

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
