import uuid
import io
import pandas as pd
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple
from decimal import Decimal
from domain.interfaces import (
    SaleRepositoryInterface, 
    FinancialTransactionRepositoryInterface,
    MarketRepositoryInterface
)
from application.dtos import FinancialReportDTO, FinancialCategorySummaryDTO
from domain.finance import TransactionType

class FinanceReportService:
    """
    Serviço especializado em geração de relatórios contábeis e exportações.
    Consolida dados de múltiplas fontes (Vendas e Lançamentos Manuais).
    """

    def __init__(
        self,
        sale_repo: SaleRepositoryInterface,
        transaction_repo: FinancialTransactionRepositoryInterface,
        market_repo: MarketRepositoryInterface
    ):
        self.sale_repo = sale_repo
        self.transaction_repo = transaction_repo
        self.market_repo = market_repo

    async def get_monthly_report(self, market_id: uuid.UUID, year: int, month: int) -> FinancialReportDTO:
        """Gera o sumário de DRE (Demonstrativo de Resultados) do mês."""
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year, 12, 31)
        else:
            end_date = date(year, month + 1, 1)

        # 1. Busca Receitas de Vendas (PDV)
        sales = await self.sale_repo.get_sales_by_period(
            market_id, 
            datetime.combine(start_date, datetime.min.time()),
            datetime.combine(end_date, datetime.max.time())
        )
        total_sales = sum((s.total_amount for s in sales if s.status.value == "concluida"), Decimal("0.00"))

        # 2. Busca Transações Manuais
        # Passando strings ISO para o repo que implementamos o filtro
        transactions = await self.transaction_repo.list_by_market(
            market_id,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat()
        )

        revenue_map = {"Vendas PDV": total_sales}
        expense_map = {}

        for t in transactions:
            category = t.category or "Diversos"
            amount = t.amount
            
            # Checa se é receita ou despesa. 
            # No TransactionType: CREDIT='receita', DEBIT='despesa'
            if t.type == TransactionType.CREDIT:
                revenue_map[category] = revenue_map.get(category, Decimal("0")) + amount
            else:
                expense_map[category] = expense_map.get(category, Decimal("0")) + amount

        total_revenue = sum(revenue_map.values())
        total_expenses = sum(expense_map.values())

        return FinancialReportDTO(
            period=f"{month:02d}/{year}",
            total_revenue=total_revenue,
            total_expenses=total_expenses,
            net_profit=total_revenue - total_expenses,
            revenue_breakdown=[FinancialCategorySummaryDTO(category=k, total=v) for k, v in revenue_map.items()],
            expense_breakdown=[FinancialCategorySummaryDTO(category=k, total=v) for k, v in expense_map.items()],
            entries_count=len(sales) + len(transactions)
        )

    async def export_to_excel(self, market_id: uuid.UUID, year: int, month: int) -> io.BytesIO:
        """
        Gera um arquivo Excel (.xlsx) com o detalhamento contábil completo.
        Utiliza tabelas nativas do Excel, formatação de moeda e quebra de texto.
        """
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year, 12, 31)
        else:
            end_date = date(year, month + 1, 1)

        # 1. Busca Dados
        sales = await self.sale_repo.get_sales_by_period(
            market_id, 
            datetime.combine(start_date, datetime.min.time()),
            datetime.combine(end_date, datetime.max.time())
        )
        
        transactions = await self.transaction_repo.list_by_market(
            market_id,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat()
        )
        
        # 2. Constrói Lista Unificada para o "Extrato Detalhado"
        extract_data = []
        
        # Adiciona Vendas (Convertendo data para datetime naive para evitar conflito de timezone no excel)
        for s in sales:
            if s.status.value != "concluida": continue
            
            methods = ", ".join([p.method.value for p in s.payments])
            # Ajuste de fuso horário simples (remove info de timezone se existir)
            dt_naive = s.created_at.replace(tzinfo=None)
            
            extract_data.append({
                "Data": dt_naive,
                "Tipo": "Entrada",
                "Origem": "Venda PDV",
                "Categoria": "Vendas",
                "Descrição": f"Venda #{str(s.id)[:8]} ({len(s.items)} itens) - {methods}",
                "Valor (R$)": float(s.total_amount)
            })
            
        # Adiciona Transações Manuais
        for t in transactions:
            is_credit = (t.type == TransactionType.CREDIT)
            tipo_str = "Entrada" if is_credit else "Saída"
            
            # Converte data
            dt_naive = t.due_date.replace(tzinfo=None) if isinstance(t.due_date, datetime) else datetime.combine(t.due_date, datetime.min.time())

            extract_data.append({
                "Data": dt_naive,
                "Tipo": tipo_str,
                "Origem": "Manual",
                "Categoria": t.category,
                "Descrição": t.description,
                "Valor (R$)": float(t.amount)
            })

        # Ordena por Data
        extract_data.sort(key=lambda x: x["Data"])

        # Cria DataFrames
        df_extract = pd.DataFrame(extract_data)
        
        # Calculando totais para o Resumo
        total_entradas = sum(x["Valor (R$)"] for x in extract_data if x["Tipo"] == "Entrada")
        total_saidas = sum(x["Valor (R$)"] for x in extract_data if x["Tipo"] == "Saída")
        
        df_summary = pd.DataFrame([
            {"Métrica": "Total Entradas", "Valor": total_entradas},
            {"Métrica": "Total Saídas", "Valor": total_saidas},
            {"Métrica": "Resultado do Período", "Valor": total_entradas - total_saidas}
        ])

        # 3. Exporta com XlsxWriter
        output = io.BytesIO()
        
        # engine='xlsxwriter' é essencial para formatação avançada
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            workbook = writer.book
            
            # --- DEFINIÇÃO DE FORMATOS ---
            money_fmt = workbook.add_format({'num_format': 'R$ #,##0.00', 'valign': 'vcenter'})
            date_fmt = workbook.add_format({'num_format': 'dd/mm/yyyy hh:mm', 'align': 'center', 'valign': 'vcenter'})
            center_fmt = workbook.add_format({'align': 'center', 'valign': 'vcenter'})
            
            # Formato crucial para a descrição: Text Wrap ativado
            text_wrap_fmt = workbook.add_format({'text_wrap': True, 'valign': 'vcenter'})
            
            # Formatos de Destaque (Cores)
            green_bg = workbook.add_format({'bg_color': '#C6EFCE', 'font_color': '#006100', 'align': 'center'})
            red_bg = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006', 'align': 'center'})
            header_fmt = workbook.add_format({'bold': True, 'text_wrap': True, 'valign': 'top', 'fg_color': '#D7E4BC', 'border': 1})

            # ========================
            # ABA: EXTRATO DETALHADO
            # ========================
            sheet_name = 'Extrato Detalhado'
            # Escreve o DataFrame sem cabeçalho padrão, pois usaremos add_table
            df_extract.to_excel(writer, sheet_name=sheet_name, index=False, startrow=1, header=False)
            ws = writer.sheets[sheet_name]
            
            (max_row, max_col) = df_extract.shape
            
            # Configuração das Colunas para a Tabela Excel
            # A ordem deve bater com as colunas do DataFrame: Data, Tipo, Origem, Categoria, Descrição, Valor
            columns_settings = [
                {'header': 'Data', 'format': date_fmt},
                {'header': 'Tipo', 'format': center_fmt},
                {'header': 'Origem', 'format': center_fmt},
                {'header': 'Categoria', 'format': center_fmt},
                {'header': 'Descrição', 'format': text_wrap_fmt}, # AQUI ATIVA O WRAP E AUTO-ALTURA
                {'header': 'Valor (R$)', 'format': money_fmt}
            ]
            
            # Cria a Tabela Excel (ListObject)
            if max_row > 0:
                ws.add_table(0, 0, max_row, max_col - 1, {
                    'columns': columns_settings,
                    'style': 'TableStyleMedium2', # Estilo Azul Profissional
                    'name': 'TabelaExtrato'
                })
            else:
                # Caso não tenha dados, escreve apenas o cabeçalho manualmente
                for col_num, value in enumerate(df_extract.columns.values):
                    ws.write(0, col_num, value, header_fmt)

            # Ajuste de Larguras de Coluna
            ws.set_column('A:A', 18) # Data
            ws.set_column('B:B', 12) # Tipo
            ws.set_column('C:C', 12) # Origem
            ws.set_column('D:D', 20) # Categoria
            ws.set_column('E:E', 50) # Descrição (Bem larga)
            ws.set_column('F:F', 18) # Valor

            # Formatação Condicional para Entrada/Saída na coluna Tipo (B)
            # Aplica estilo verde/vermelho
            ws.conditional_format(1, 1, max_row, 1, {
                'type': 'text',
                'criteria': 'containing',
                'value': 'Entrada',
                'format': green_bg
            })
            ws.conditional_format(1, 1, max_row, 1, {
                'type': 'text',
                'criteria': 'containing',
                'value': 'Saída',
                'format': red_bg
            })

            # ========================
            # ABA: RESUMO
            # ========================
            sheet_resumo = 'Resumo'
            df_summary.to_excel(writer, sheet_name=sheet_resumo, index=False, startrow=1, header=False)
            ws_resumo = writer.sheets[sheet_resumo]
            
            (r_rows, r_cols) = df_summary.shape
            
            summary_cols = [
                {'header': 'Métrica', 'format': center_fmt},
                {'header': 'Valor (R$)', 'format': money_fmt}
            ]
            
            ws_resumo.add_table(0, 0, r_rows, r_cols - 1, {
                'columns': summary_cols,
                'style': 'TableStyleMedium9', # Estilo Cinza
                'name': 'TabelaResumo'
            })
            
            ws_resumo.set_column('A:A', 30)
            ws_resumo.set_column('B:B', 20)

        output.seek(0)
        return output

    async def export_to_pdf(self, market_id: uuid.UUID, year: int, month: int) -> io.BytesIO:
        """
        Gera um PDF formatado para impressão. 
        Nota: Em ambiente real requer fpdf2 ou reportlab instalados.
        """
        report = await self.get_monthly_report(market_id, year, month)
        market = await self.market_repo.get_by_id(market_id)

        output = io.BytesIO()
        # Mock de PDF
        output.write(b"%PDF-1.4\n")
        output.write(f"Relatorio Financeiro Detalhado - {market.name} - {report.period}\n".encode())
        output.write(f"Receita Total: R$ {report.total_revenue}\n".encode())
        output.write(f"Despesas Totais: R$ {report.total_expenses}\n".encode())
        output.write(f"Lucro Liquido: R$ {report.net_profit}\n".encode())
        output.seek(0)
        return output