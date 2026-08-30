#property copyright "GBPUSD Session Research"
#property version   "1.00"
#property script_show_inputs
#property strict

// Run this script while MT5 is connected to the Exness Raw Spread account.
// The end timestamp is exclusive. Exness MT5 server time is UTC/GMT+0.
input string   InpSymbol    = "";
input datetime InpStartUtc  = D'2024.01.01 00:00:00';
input datetime InpEndUtc    = D'2026.08.01 00:00:00';
input int      InpChunkDays = 1;

datetime StartOfNextMonth(const datetime value)
  {
   MqlDateTime parts;
   TimeToStruct(value,parts);
   parts.day=1;
   parts.hour=0;
   parts.min=0;
   parts.sec=0;
   if(parts.mon==12)
     {
      parts.year++;
      parts.mon=1;
     }
   else
      parts.mon++;
   return StructToTime(parts);
  }

string SafeSymbol(string value)
  {
   StringReplace(value,"/","_");
   StringReplace(value,"\\","_");
   StringReplace(value," ","_");
   return value;
  }

bool ExportMonth(const string symbol,
                 const datetime range_start,
                 const datetime range_end,
                 const int chunk_days)
  {
   MqlDateTime parts;
   TimeToStruct(range_start,parts);
   string filename=StringFormat("phase9_%s_%04d-%02d.csv",
                                SafeSymbol(symbol),parts.year,parts.mon);
   int handle=FileOpen(filename,FILE_WRITE|FILE_CSV|FILE_ANSI,',');
   if(handle==INVALID_HANDLE)
     {
      PrintFormat("EXPORT_FAILED: FileOpen(%s), error=%d",filename,GetLastError());
      return false;
     }

   FileWrite(handle,"source","symbol","time_msc","bid","ask","last",
             "volume","volume_real","flags");
   long total=0;
   datetime chunk_start=range_start;
   while(chunk_start<range_end)
     {
      datetime chunk_end=chunk_start+(datetime)(chunk_days*86400);
      if(chunk_end>range_end)
         chunk_end=range_end;

      MqlTick ticks[];
      ResetLastError();
      ulong from_msc=(ulong)chunk_start*1000;
      ulong to_msc=(ulong)chunk_end*1000-1;
      int copied=CopyTicksRange(symbol,ticks,COPY_TICKS_INFO,from_msc,to_msc);
      int error=GetLastError();
      if(copied<0 || error==4403 || error==4407)
        {
         PrintFormat("EXPORT_FAILED: %s to %s, copied=%d, error=%d",
                     TimeToString(chunk_start,TIME_DATE|TIME_SECONDS),
                     TimeToString(chunk_end,TIME_DATE|TIME_SECONDS),copied,error);
         FileClose(handle);
         return false;
        }

      int digits=(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS);
      for(int index=0; index<copied; index++)
        {
         FileWrite(handle,"Exness-MT5",symbol,(long)ticks[index].time_msc,
                   DoubleToString(ticks[index].bid,digits),
                   DoubleToString(ticks[index].ask,digits),
                   DoubleToString(ticks[index].last,digits),
                   (long)ticks[index].volume,
                   DoubleToString(ticks[index].volume_real,8),
                   (long)ticks[index].flags);
        }
      total+=copied;
      FileFlush(handle);
      PrintFormat("%s: %s to %s, rows=%d",
                  filename,
                  TimeToString(chunk_start,TIME_DATE|TIME_SECONDS),
                  TimeToString(chunk_end,TIME_DATE|TIME_SECONDS),copied);
      chunk_start=chunk_end;
     }

   FileClose(handle);
   PrintFormat("EXPORT_OK: %s, total rows=%d",filename,total);
   return true;
  }

void OnStart()
  {
   string symbol=InpSymbol;
   if(StringLen(symbol)==0)
      symbol=_Symbol;
   if(InpEndUtc<=InpStartUtc)
     {
      Print("EXPORT_FAILED: InpEndUtc must be later than InpStartUtc");
      return;
     }
   if(InpChunkDays<1 || InpChunkDays>7)
     {
      Print("EXPORT_FAILED: InpChunkDays must be between 1 and 7");
      return;
     }
   if(!SymbolSelect(symbol,true))
     {
      PrintFormat("EXPORT_FAILED: cannot select symbol %s, error=%d",
                  symbol,GetLastError());
      return;
     }

   datetime month_start=InpStartUtc;
   while(month_start<InpEndUtc)
     {
      datetime next_month=StartOfNextMonth(month_start);
      datetime month_end=next_month<InpEndUtc ? next_month : InpEndUtc;
      if(!ExportMonth(symbol,month_start,month_end,InpChunkDays))
        {
         Print("EXPORT_ABORTED: fix the reported history/file error and rerun");
         return;
        }
      month_start=month_end;
     }
   Print("EXPORT_COMPLETE: all requested months written to MQL5/Files");
  }
