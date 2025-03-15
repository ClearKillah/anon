import re
import sys

def find_try_without_except(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    line_num = 0
    errors = []
    
    # Простая эвристика: найти строки с "try:" и проверить, есть ли после них "except" или "finally"
    # с соответствующим отступом до следующего блока того же уровня отступа
    while line_num < len(lines):
        line = lines[line_num]
        match = re.match(r'^(\s*)try\s*:', line)
        
        if match:
            indent = match.group(1)  # Получаем отступ
            try_line = line_num + 1
            
            # Ищем соответствующий except или finally с тем же отступом
            found_except_or_finally = False
            nested_level = 0
            
            for i in range(line_num + 1, len(lines)):
                next_line = lines[i]
                
                # Пропускаем пустые строки и комментарии
                if not next_line.strip() or next_line.strip().startswith('#'):
                    continue
                
                # Если находим строку с тем же отступом, это может быть конец блока try
                if next_line.startswith(indent) and not next_line.startswith(indent + ' '):
                    # Если не нашли except или finally, это ошибка
                    if not found_except_or_finally:
                        errors.append((try_line, f"try block at line {try_line} without except or finally"))
                    break
                
                # Проверяем, есть ли except или finally с тем же отступом
                if (next_line.startswith(indent + 'except') or
                    next_line.startswith(indent + 'finally')):
                    found_except_or_finally = True
                    
                # Если мы достигли конца файла, проверяем, был ли except или finally
                if i == len(lines) - 1 and not found_except_or_finally:
                    errors.append((try_line, f"try block at line {try_line} without except or finally"))
        
        line_num += 1
    
    return errors

def main():
    if len(sys.argv) < 2:
        print("Usage: python find_try_errors.py <filename>")
        return
    
    filename = sys.argv[1]
    errors = find_try_without_except(filename)
    
    if errors:
        print(f"Found {len(errors)} try blocks without except or finally:")
        for line, error in errors:
            print(f"Line {line}: {error}")
    else:
        print("No try blocks without except or finally found.")

if __name__ == "__main__":
    main() 