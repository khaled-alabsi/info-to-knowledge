-- A pandoc filter for the office workflow: a table with a merged cell (a cell spanning
-- columns or rows) is written as an HTML table, which keeps the span; every other table is left to the markdown
-- writer, which writes it as a plain pipe table. Without it, GitHub markdown writes a table with a merged cell as a
-- pipe table and drops the span.

local function has_merged_cell(rows)
  for _, row in ipairs(rows) do
    for _, cell in ipairs(row.cells) do
      if cell.col_span > 1 or cell.row_span > 1 then
        return true
      end
    end
  end
  return false
end

local function table_has_merged_cell(tbl)
  if has_merged_cell(tbl.head.rows) or has_merged_cell(tbl.foot.rows) then
    return true
  end
  for _, body in ipairs(tbl.bodies) do
    if has_merged_cell(body.head) or has_merged_cell(body.body) then
      return true
    end
  end
  return false
end

function Table(tbl)
  if table_has_merged_cell(tbl) then
    local html = pandoc.write(pandoc.Pandoc({tbl}), "html")
    return pandoc.RawBlock("html", html)
  end
  return nil
end
