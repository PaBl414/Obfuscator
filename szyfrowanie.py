import string
import unicodedata


def korekcja_formatu(k):
    """Zamienia polskie znaki, J na I, na wielkie litery, usuwa duplikaty."""
    k = k.upper()
    k = k.replace('Ł', 'L')
    k = k.replace('J', 'I')
    k = unicodedata.normalize('NFKD', k)
    return ''.join(dict.fromkeys(''.join(c for c in k if not unicodedata.combining(c))))


def zrob_tablice(klucz1):
    """Tworzy szachownicę Polibiusza 5x5 z klucza."""
    alfabet = korekcja_formatu(klucz1 + string.ascii_uppercase)
    alfabet = alfabet[:25]
    if len(alfabet) < 25:
        raise ValueError(f"Alfabet ma tylko {len(alfabet)} znaków, potrzeba 25")
    return [list(alfabet[i:i+5]) for i in range(0, 25, 5)]


def pole(tablica, znak):
    """Zwraca numer 11–55 dla znaku w tablicy 5×5."""
    znak = korekcja_formatu(znak)
    if not znak:
        return None
    for i, c in enumerate([c for wiersz in tablica for c in wiersz]):
        if c == znak:
            return (i // 5 + 1) * 10 + (i % 5 + 1)
    raise ValueError(f"Znaku '{znak}' nie ma w tablicy Polibiusza")


def szyfruj(tekst, klucz1, klucz2):
    """Szyfruje tekst szyfrem Nihilist."""
    tablica = zrob_tablice(klucz1)
    
    tekst_liczby = []
    for c in korekcja_formatu(tekst):
        if c in string.ascii_uppercase:
            tekst_liczby.append(pole(tablica, c))
    
    klucz_liczby = [pole(tablica, c) for c in korekcja_formatu(klucz2)]
    
    szyfrogram = []
    for i, liczba in enumerate(tekst_liczby):
        szyfrogram.append(liczba + klucz_liczby[i % len(klucz_liczby)])
    
    return szyfrogram


def deszyfruj(szyfrogram, klucz1, klucz2):
    """Deszyfruje szyfrogram Nihilist."""
    tablica = zrob_tablice(klucz1)
    
    klucz_liczby = [pole(tablica, c) for c in korekcja_formatu(klucz2)]
    
    tekst_liczby = []
    for i, liczba in enumerate(szyfrogram):
        tekst_liczby.append(liczba - klucz_liczby[i % len(klucz_liczby)])
    
    tekst = ""
    for liczba in tekst_liczby:
        wiersz = liczba // 10
        kolumna = liczba % 10
        tekst += tablica[wiersz - 1][kolumna - 1]
    
    return tekst


# ==================== PRZYKŁAD ====================
if __name__ == "__main__":
    klucz1 = "ZEBRAS"
    klucz2 = "RUSSIAN"
    tekst = "DYNAMITE WINTER PALACE"
    
    print("Tablica Polibiusza:")
    tablica = zrob_tablice(klucz1)
    for wiersz in tablica:
        print(wiersz)
    
    szyfrogram = szyfruj(tekst, klucz1, klucz2)
    print(f"\nSzyfrogram: {' '.join(map(str, szyfrogram))}")
    
    odszyfrowany = deszyfruj(szyfrogram, klucz1, klucz2)
    print(f"Odszyfrowany: {odszyfrowany}")